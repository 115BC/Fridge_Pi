import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("FRIDGE_DATA_DIR") or
                (Path(__file__).resolve().parent / "data"))
DB_PATH = DATA_DIR / "fridge.db"

# 微信云托管内置 MySQL：控制台开启后注入 MYSQL_ADDRESS 等变量，命中即用 MySQL；
# 本地/树莓派无这些变量时保持 SQLite。
USING_MYSQL = bool(os.environ.get("MYSQL_ADDRESS"))
MYSQL_DB = os.environ.get("MYSQL_DATABASE") or "fridge_pi"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  openid TEXT UNIQUE NOT NULL,
  name TEXT DEFAULT '',
  room TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'student',   -- student | manager | admin
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id),
  fridge_id TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT DEFAULT '',
  quantity INTEGER DEFAULT 1,
  expire_at TEXT NOT NULL,                 -- 到期日期 YYYY-MM-DD
  status TEXT DEFAULT 'active',            -- active | pending_claim | taken_out | removed
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS unlock_commands(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER NOT NULL REFERENCES items(id),
  fridge_id TEXT NOT NULL,
  code TEXT NOT NULL,
  ts INTEGER NOT NULL,
  token TEXT NOT NULL,
  status TEXT DEFAULT 'pending',           -- pending | dispatched | unlocked | rejected | expired
  result_reason TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  acked_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id INTEGER, user_id INTEGER, actor_id INTEGER,
  channel TEXT DEFAULT 'subscribe',
  status TEXT DEFAULT 'sent',              -- sent | failed | skipped
  detail TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS pi_nodes(
  fridge_id TEXT PRIMARY KEY,
  is_locked INTEGER, mock INTEGER, version TEXT,
  payload TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT, actor TEXT, detail TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buildings(
  name TEXT PRIMARY KEY,                 -- 楼宇名（学生选择、宿管筛选用）
  fridge_id TEXT NOT NULL                -- 该楼冰箱=树莓派设备，物品/容量按此归属
);
CREATE TABLE IF NOT EXISTS photos(
  name TEXT PRIMARY KEY,                 -- 盘点/清理照片：云托管容器文件系统发布即清空，必须落库
  data BLOB NOT NULL,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""

# 与 SQLite 版一一对应；时间列用 DATETIME 保持 NOW() 默认值语义；
# 不建外键（迁移时按表序插入即可，避免约束顺序问题）。
SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS users(
  id INT PRIMARY KEY AUTO_INCREMENT,
  openid VARCHAR(64) NOT NULL UNIQUE,
  name VARCHAR(64) DEFAULT '',
  room VARCHAR(64) DEFAULT '',
  phone VARCHAR(32) DEFAULT '',
  role VARCHAR(16) NOT NULL DEFAULT 'student',
  quota INT NOT NULL DEFAULT 5,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS items(
  id INT PRIMARY KEY AUTO_INCREMENT,
  code VARCHAR(16) NOT NULL UNIQUE,
  user_id INT NOT NULL,
  fridge_id VARCHAR(64) NOT NULL,
  name VARCHAR(128) NOT NULL,
  category VARCHAR(64) DEFAULT '',
  quantity INT DEFAULT 1,
  expire_at VARCHAR(16) NOT NULL,
  status VARCHAR(16) DEFAULT 'active',
  volume_ml INT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS unlock_commands(
  id INT PRIMARY KEY AUTO_INCREMENT,
  item_id INT NOT NULL,
  fridge_id VARCHAR(64) NOT NULL,
  code VARCHAR(16) NOT NULL,
  ts BIGINT NOT NULL,
  token VARCHAR(128) NOT NULL,
  status VARCHAR(16) DEFAULT 'pending',
  result_reason VARCHAR(255) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  acked_at DATETIME
);
CREATE TABLE IF NOT EXISTS reminders(
  id INT PRIMARY KEY AUTO_INCREMENT,
  item_id INT, user_id INT, actor_id INT,
  channel VARCHAR(16) DEFAULT 'subscribe',
  status VARCHAR(16) DEFAULT 'sent',
  detail VARCHAR(512) DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pi_nodes(
  fridge_id VARCHAR(64) PRIMARY KEY,
  is_locked TINYINT, mock TINYINT, version VARCHAR(32),
  payload TEXT, last_seen DATETIME
);
CREATE TABLE IF NOT EXISTS events(
  id INT PRIMARY KEY AUTO_INCREMENT,
  type VARCHAR(32), actor VARCHAR(64), detail TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings(
  `key` VARCHAR(64) PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS buildings(
  name VARCHAR(64) PRIMARY KEY,
  fridge_id VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS photos(
  name VARCHAR(128) PRIMARY KEY,
  data LONGBLOB NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

TABLES = ("users", "buildings", "items", "unlock_commands", "reminders", "pi_nodes", "events", "settings")

_local = threading.local()


def _mysql_connect():
    import pymysql

    host, _, port = os.environ["MYSQL_ADDRESS"].partition(":")
    kw = dict(host=host, port=int(port or 3306),
              user=os.environ.get("MYSQL_USERNAME") or "root",
              password=os.environ.get("MYSQL_PASSWORD") or "",
              charset="utf8mb4", autocommit=True,
              connect_timeout=8, read_timeout=15, write_timeout=15)
    try:
        return pymysql.connect(database=MYSQL_DB, **kw)
    except Exception:
        c = pymysql.connect(**kw)
        c.cursor().execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` CHARACTER SET utf8mb4")
        c.select_db(MYSQL_DB)
        return c


def _norm_val(v):
    # DATETIME 列 pymysql 返回 datetime 对象，统一转字符串与 SQLite 的 JSON 形态一致
    import datetime as _dt
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return str(v)
    return v


class _MyRow:
    """兼容 sqlite3.Row：既支持 row["col"] 也支持 row[0]，dict(row) 可用。"""

    def __init__(self, keys, vals):
        vals = [_norm_val(v) for v in vals]
        self._keys = keys
        self._vals = vals
        self._map = dict(zip(keys, vals))

    def __getitem__(self, k):
        return self._vals[k] if isinstance(k, int) else self._map[k]

    def keys(self):
        return self._keys

    def get(self, k, default=None):
        return self._map.get(k, default)

    def __contains__(self, k):
        return k in self._map

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)


class _MyResult:
    def __init__(self, cur):
        self._cur = cur
        self._keys = [d[0] for d in cur.description] if cur.description else []
        self.lastrowid = cur.lastrowid

    def fetchone(self):
        r = self._cur.fetchone()
        return _MyRow(self._keys, r) if r is not None else None

    def fetchall(self):
        return [_MyRow(self._keys, r) for r in self._cur.fetchall()]


def _to_mysql(sql: str) -> str:
    sql = sql.replace("datetime('now','localtime')", "NOW()")
    # key 是 MySQL 保留字，settings 表的语句需反引号
    sql = sql.replace("settings(key, value)", "settings(`key`, value)")
    sql = sql.replace("WHERE key=?", "WHERE `key`=?")
    m = re.search(r"ON CONFLICT\([^)]*\)\s*DO UPDATE\s+SET\s+(.*)", sql, re.S | re.I)
    if m:
        sets = re.sub(r"excluded\.(\w+)", r"VALUES(\1)", m.group(1))
        sql = sql[:m.start()] + "ON DUPLICATE KEY UPDATE " + sets
    return sql.replace("?", "%s")


class _MyConn:
    """每线程一条 MySQL 连接（uvicorn 线程池复用，避免逐请求建连泄漏）。"""

    def __init__(self):
        c = getattr(_local, "conn", None)
        if c is None:
            c = _mysql_connect()
            _local.conn = c
        c.ping(reconnect=True)
        self._c = c

    def execute(self, sql, args=()):
        cur = self._c.cursor()
        if args:
            cur.execute(_to_mysql(sql), tuple(args))
        else:
            cur.execute(_to_mysql(sql))
        return _MyResult(cur)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def conn():
    if USING_MYSQL:
        return _MyConn()
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)  # 自动提交
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=8000")
    return c


def init():
    if USING_MYSQL:
        c = conn()
        for stmt in SCHEMA_MYSQL.split(";"):
            if stmt.strip():
                c.execute(stmt)
        try:
            c.execute("ALTER TABLE users ADD COLUMN building VARCHAR(64) DEFAULT ''")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE items ADD COLUMN escalated INT DEFAULT 0")
        except Exception:
            pass
    else:
        with conn() as c:
            c.executescript(SCHEMA)
            try:
                c.execute("ALTER TABLE items ADD COLUMN volume_ml INTEGER")  # 人工修正体积，NULL=按类别先验
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE users ADD COLUMN quota INTEGER NOT NULL DEFAULT 5")  # 在库件数上限
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE users ADD COLUMN building TEXT DEFAULT ''")  # 所属楼宇，空=待补登记
            except sqlite3.OperationalError:
                pass
            try:
                c.execute("ALTER TABLE items ADD COLUMN escalated INTEGER DEFAULT 0")  # 超期未处理标记
            except sqlite3.OperationalError:
                pass
    if not conn().execute("SELECT name FROM buildings LIMIT 1").fetchone():
        conn().execute("INSERT INTO buildings(name, fridge_id) VALUES(?, ?)", ("主楼", "fridge-01"))


def get_setting(key: str, default: str = "") -> str:
    row = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with conn() as c:
        c.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def log_event(type_: str, actor: str, detail: str = ""):
    with conn() as c:
        c.execute("INSERT INTO events(type, actor, detail) VALUES(?,?,?)",
                  (type_, actor, detail))


# ---------- 照片（盘点/清理留痕）：容器文件系统不可靠，统一落库 ----------
def save_photo(name: str, blob: bytes):
    with conn() as c:
        c.execute("INSERT INTO photos(name, data) VALUES(?, ?) "
                  "ON CONFLICT(name) DO UPDATE SET data=excluded.data", (name, blob))


def load_photo(name: str):
    row = conn().execute("SELECT data FROM photos WHERE name=?", (name,)).fetchone()
    return bytes(row["data"]) if row else None


def delete_old_photos(days: int):
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn().execute("DELETE FROM photos WHERE created_at < ?", (cutoff,))
