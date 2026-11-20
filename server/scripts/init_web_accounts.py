"""网站账号初始化：给尚无 web_username 的用户生成登录名+初始密码。
   本地：python scripts/init_web_accounts.py
   云端 MySQL：在云托管环境里跑一次（或本地设 MYSQL_ADDRESS 等环境变量连云端库）。
   初始密码只打印到 stdout 一次，脚本不写文件、不记日志；用户首次登录须改密。"""
import os
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db
from auth import hash_password

alphabet = string.ascii_letters + string.digits
c = db.conn()
rows = c.execute("SELECT id, name, room, role, web_username FROM users ORDER BY id").fetchall()
created = 0
for r in rows:
    if r["web_username"] or r["name"] in ("未认领", ""):
        continue
    base = (r["name"] + "-" + r["room"]).strip("-") or f"user{r['id']}"
    base = "".join(ch for ch in base if ch.isalnum() or ch in "-_")[:40]
    username = base
    n = 2
    while c.execute("SELECT id FROM users WHERE web_username=?", (username,)).fetchone():
        username = f"{base}-{n}"
        n += 1
    password = "".join(secrets.choice(alphabet) for _ in range(10))
    c.execute("UPDATE users SET web_username=?, password_hash=?, must_change_password=1 WHERE id=?",
              (username, hash_password(password), r["id"]))
    print(f"{r['id']:>3}  {r['role']:<7} {r['name']}/{r['room']:<8} "
          f"账号={username:<20} 初始密码={password}")
    created += 1
print(f"\n共开通 {created} 个网站账号；初始密码仅显示这一次，请分发后让用户首次登录修改。")