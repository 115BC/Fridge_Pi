"""邮箱注册/验证码/绑定/重置 单元测试（不需要起服务）：
   python scripts/test_email.py
"""
import os
import re
import tempfile
from pathlib import Path

sys_path = str(Path(__file__).resolve().parent.parent)
import sys
sys.path.insert(0, sys_path)
os.environ["FRIDGE_DATA_DIR"] = tempfile.mkdtemp(prefix="fridge_email_")

import db
import emailer
import main
from fastapi import HTTPException

ok = []
sent = []


def check(name, cond, extra=""):
    ok.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, extra)


def fake_send(to, subject, body):
    sent.append((to, subject, body))
    m = re.search(r"验证码是：(\d{6})", body)
    if m:
        sent[-1] = (to, subject, body, m.group(1))
    elif "重置为：" in body:
        m = re.search(r"重置为：\s*([A-Za-z0-9]{12})", body)
        sent[-1] = (to, subject, body, m.group(1) if m else "")


emailer.send_mail = fake_send
db.init()

EMAIL = "202411081071@mail.bnu.edu.cn"
SID = "202411081071"

# 1. 邮箱与学号规则
check("合法校园邮箱规范化", emailer.normalized_email(EMAIL) == EMAIL)
check("学号提取", emailer.student_id_of(EMAIL) == SID)
for bad in ["202411081071@qq.com", "abc@mail.bnu.edu.cn", "2024a@mail.bnu.edu.cn"]:
    try:
        emailer.normalized_email(bad)
        check(f"非法邮箱被拒 {bad}", False)
    except ValueError:
        check(f"非法邮箱被拒 {bad}", True)

# 2. 发送+校验验证码
emailer.send_code(EMAIL, "register")
code = sent[-1][3]
check("验证码已发送且为6位数字", bool(code) and len(code) == 6, str(code))
ok_code, msg = emailer.verify_code(EMAIL, code, "register")
check("正确验证码通过", ok_code, msg)
check("验证码一次性", not emailer.verify_code(EMAIL, code, "register")[0])
emailer.send_code(EMAIL, "register")
code2 = sent[-1][3]
check("错误验证码被拒", not emailer.verify_code(EMAIL, "000000" if code2 != "000000" else "111111", "register")[0])
check("错误尝试计数增加", emailer.verify_code(EMAIL, code2, "register")[0])  # still valid after 1 wrong

# 3. 限流
can, why = emailer.can_send(EMAIL)
check("60秒内不能重发", not can, why)

# 4. 注册接口
email3 = "202211081072@mail.bnu.edu.cn"
emailer.send_code(email3, "register")
code3 = sent[-1][3]
r = main.web_register(main.RegisterReq(name="测试学生", room="1-101", email=email3,
                                       code=code3, password="Stu@123456"))
check("注册成功签发 JWT", bool(r["token"]) and r["role"] == "student")
check("注册登录名=学号", r["user"]["web_username"] == "202211081072", str(r["user"].get("web_username")))
check("注册写入邮箱与学号", r["user"]["email"] == email3 and r["user"]["student_id"] == "202211081072")
check("注册后密码可登录",
      main.web_login(main.WebLoginReq(username="202211081072", password="Stu@123456"))["role"] == "student")

# 5. 重复邮箱注册被拒
emailer.send_code(email3, "register")
code_dup = sent[-1][3]
try:
    main.web_register(main.RegisterReq(name="重复", room="1-102", email=email3,
                                       code=code_dup, password="Stu@123456"))
    check("重复邮箱注册被拒", False)
except HTTPException as e:
    check("重复邮箱注册被拒", e.status_code == 400)

# 6. 旧账号补绑邮箱
user = dict(db.conn().execute("SELECT * FROM users WHERE id=?", (r["user"]["id"],)).fetchone())
email4 = "202211081073@mail.bnu.edu.cn"
emailer.send_code(email4, "bind")
code4 = sent[-1][3]
res = main.web_bind_email(main.BindEmailReq(email=email4, code=code4), user)
check("补绑邮箱成功", res["student_id"] == "202211081073", str(res))

# 7. 忘记密码重置
emailer.send_code(email4, "reset")
code5 = sent[-1][3]
main.web_reset_password(main.ResetPasswordReq(email=email4, code=code5,
                                              new_password="NewPass@789"))
check("重置后新密码可登录",
      main.web_login(main.WebLoginReq(username="202211081072",
                                      password="NewPass@789"))["role"] == "student")

# 8. 超管重置密码：随机密码发邮件，响应不含密码
admin = dict(db.conn().execute("SELECT * FROM users WHERE role='admin' LIMIT 1").fetchone()) \
    if db.conn().execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone() else None
if not admin:
    cur = db.conn().execute("INSERT INTO users(openid,name,room,role,web_username,password_hash) "
                            "VALUES('test_admin','超管','值班室','admin','admin',?)",
                            (__import__("auth").hash_password("Admin@123"),))
    admin = dict(db.conn().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
before = len(sent)
res = main.admin_reset_password(r["user"]["id"], admin)
newpw = sent[-1][3] if len(sent) > before else ""
check("超管重置发了邮件", len(sent) > before, "")
check("随机密码12位", bool(newpw) and len(newpw) == 12, str(newpw))
check("响应不含密码", "password" not in str(res).lower() and newpw not in str(res), str(res))
check("随机密码可登录",
      main.web_login(main.WebLoginReq(username="202211081072", password=newpw))["role"] == "student")

n_fail = sum(1 for _, v in ok if not v)
print(f"\n===== 邮箱注册测试: {len(ok)-n_fail}/{len(ok)} 通过 =====")
raise SystemExit(1 if n_fail else 0)
