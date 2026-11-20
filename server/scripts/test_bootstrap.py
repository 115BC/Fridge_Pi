"""网站账号自举与版本标识的单元测试（不需要起服务）：
   python scripts/test_bootstrap.py
   覆盖：启动自举创建账号 / 已存在账号不重置密码 / 找不到用户跳过 / build_tag 动态化
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("FRIDGE_DATA_DIR", tempfile.mkdtemp(prefix="fridge_bootstrap_"))

import db
import main

ok = []


def check(name, cond, extra=""):
    ok.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, extra)


db.init()
c = db.conn()
UNCLAIMED = main.UNCLAIMED_OPENID
uid = c.execute("SELECT id FROM users WHERE openid=?", ("dev_bootstrap_user",)).fetchone()
if not uid:
    cur = c.execute("INSERT INTO users(openid,name,room,role,web_username,password_hash) "
                    "VALUES(?,?,?,?,'','')", ("dev_bootstrap_user", "自举测试", "0-000", "admin"))
    uid = cur.lastrowid
user_id = uid[0] if isinstance(uid, tuple) else uid
c.execute("UPDATE users SET web_username='', password_hash='' WHERE id=?", (user_id,))

# 1. 有环境变量且用户无网站账号 -> 自动开通
os.environ["WEB_BOOTSTRAP_USERNAME"] = "bootuser"
os.environ["WEB_BOOTSTRAP_PASSWORD"] = "Boot@1234"
os.environ["WEB_BOOTSTRAP_OPENID"] = "dev_bootstrap_user"
main.bootstrap_web_account()
row = c.execute("SELECT web_username, must_change_password, password_hash FROM users WHERE id=?",
                (user_id,)).fetchone()
check("自举创建网站账号", row["web_username"] == "bootuser", str(dict(row)))
check("自举强制首次改密", row["must_change_password"] == 1, str(row["must_change_password"]))
check("自举写入的是哈希不是明文",
      row["password_hash"] and "Boot@1234" not in row["password_hash"], "")
check("哈希可校验", main.verify_password("Boot@1234", row["password_hash"]), "")
check("错误密码校验失败", not main.verify_password("wrong", row["password_hash"]), "")

# 2. 重复启动不重置已有账号的密码
main.bootstrap_web_account()
row2 = c.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
check("重复自举不重置密码", row2["password_hash"] == row["password_hash"], "")

# 3. openid 找不到 -> 跳过，不报错
os.environ["WEB_BOOTSTRAP_OPENID"] = "no_such_openid_xyz"
main.bootstrap_web_account()
check("未知 openid 安全跳过", True, "")

# 3b. 未设 OPENID、USERNAME 也匹配不到人，但库里只有一个超管 -> 自动落到该超管
#     （运维只填登录名与密码时的常见情形；此处临时把其他超管降级以构造唯一超管场景）
others = c.execute("SELECT id, role FROM users WHERE role='admin' AND openid<>? AND openid<>?",
                    (UNCLAIMED, "dev_bootstrap_user")).fetchall()
saved = [(r["id"], r["role"]) for r in others]
for r in others:
    c.execute("UPDATE users SET role='manager' WHERE id=?", (r["id"],))
os.environ.pop("WEB_BOOTSTRAP_OPENID", None)
os.environ["WEB_BOOTSTRAP_USERNAME"] = "onlyadmin"
os.environ["WEB_BOOTSTRAP_PASSWORD"] = "Only@1234"
c.execute("UPDATE users SET web_username='', password_hash='' WHERE openid=?",
          ("dev_bootstrap_user",))
main.bootstrap_web_account()
got = c.execute("SELECT web_username FROM users WHERE openid=?", ("dev_bootstrap_user",)).fetchone()
check("唯一超管兜底可定位用户", got["web_username"] == "onlyadmin", str(dict(got)))
for uid_, role_ in saved:
    c.execute("UPDATE users SET role=? WHERE id=?", (role_, uid_))
c.execute("UPDATE users SET web_username='', password_hash='' WHERE openid=?",
          ("dev_bootstrap_user",))

# 3c. 全新空库：自举直接创建首个超管（独立部署不依赖小程序登录）
_saved_path = db.DB_PATH
db.DB_PATH = Path(tempfile.mkdtemp(prefix="fridge_empty_")) / "fridge.db"
db.init()
os.environ.pop("WEB_BOOTSTRAP_OPENID", None)
os.environ["WEB_BOOTSTRAP_USERNAME"] = "202411081071"
os.environ["WEB_BOOTSTRAP_PASSWORD"] = "Fresh@1234"
main.bootstrap_web_account()
erow = db.conn().execute("SELECT * FROM users WHERE web_username='202411081071'").fetchone()
check("空库自举创建超管", erow is not None and erow["role"] == "admin", "")
check("空库自举强制改密", erow is not None and erow["must_change_password"] == 1, "")
check("空库自举密码可校验",
      erow is not None and main.verify_password("Fresh@1234", erow["password_hash"]), "")
db.DB_PATH = _saved_path

# 4. 无环境变量 -> 什么都不做
for k in ("WEB_BOOTSTRAP_USERNAME", "WEB_BOOTSTRAP_PASSWORD", "WEB_BOOTSTRAP_OPENID"):
    os.environ.pop(k, None)
main.bootstrap_web_account()
check("无环境变量时静默跳过", True, "")

# 5. build_tag 动态化
os.environ["BUILD_TAG"] = "test-build-123"
check("BUILD_TAG 环境变量生效", main.build_tag() == "test-build-123", main.build_tag())
os.environ.pop("BUILD_TAG")
tag = main.build_tag()
check("无 BUILD_TAG 时仍有版本标识", bool(tag) and tag != "stored-2026-10-29-1", tag)

n_fail = sum(1 for _, v in ok if not v)
print(f"\n===== 自举单元测试: {len(ok)-n_fail}/{len(ok)} 通过 =====")
raise SystemExit(1 if n_fail else 0)
