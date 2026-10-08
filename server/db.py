import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "fridge.db"

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
"""


def conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)  # 自动提交
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=8000")
    return c


def init():
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
