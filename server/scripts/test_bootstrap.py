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
uid = c.execute("SELECT id FROM users WHERE openid=?", ("dev_bootstrap_user",)).fetchone()
if not uid:
    cur = c.execute("INSERT INTO users(openid,name,room,role,web_username,password_hash) "
                    "VALUES(?,?,?,?,'','')", ("dev_bootstrap_user", "自举测试", "0-000", "admin"))
    uid = cur.lastrowid
user_id = uid[0] if isinstance(uid, tuple) else uid
c.execute("UPDATE users SET web_username='', password_hash='' WHERE id=?", (user_id,))

# 1. 有环境变量且用户无网站账号 -> 自动开通
os.environ["WEB_BOOTSTRAP_USERNAME"] = "bootuser"
os.environ["WEB_BOOTSTRAP_PASSWORD"] = "boot123456"
os.environ["WEB_BOOTSTRAP_OPENID"] = "dev_bootstrap_user"
main.bootstrap_web_account()
row = c.execute("SELECT web_username, must_change_password, password_hash FROM users WHERE id=?",
                (user_id,)).fetchone()
check("自举创建网站账号", row["web_username"] == "bootuser", str(dict(row)))
check("自举强制首次改密", row["must_change_password"] == 1, str(row["must_change_password"]))
check("自举写入的是哈希不是明文",
      row["password_hash"] and "boot123456" not in row["password_hash"], "")
check("哈希可校验", main.verify_password("boot123456", row["password_hash"]), "")
check("错误密码校验失败", not main.verify_password("wrong", row["password_hash"]), "")

# 2. 重复启动不重置已有账号的密码
main.bootstrap_web_account()
row2 = c.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
check("重复自举不重置密码", row2["password_hash"] == row["password_hash"], "")

# 3. openid 找不到 -> 跳过，不报错
os.environ["WEB_BOOTSTRAP_OPENID"] = "no_such_openid_xyz"
main.bootstrap_web_account()
check("未知 openid 安全跳过", True, "")

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
