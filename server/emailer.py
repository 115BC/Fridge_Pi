"""校园邮箱验证码服务：SMTP 发信 + 验证码生成/校验/限流。
   凭据只从环境变量读取（SMTP_HOST/PORT/USER/PASSWORD/FROM），不写入仓库或日志。
   本地未配置 SMTP 时，验证码打印到后端日志（仅开发环境，方便联调）。"""
import hashlib
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

import db

SCHOOL_DOMAIN = "@mail.bnu.edu.cn"
CODE_TTL_MINUTES = 30
CODE_MAX_ATTEMPTS = 5
SEND_COOLDOWN_SECONDS = 60
SEND_DAILY_LIMIT = 10


def normalized_email(raw: str) -> str:
    """校验并返回规范化校园邮箱；学号前缀必须是纯数字。"""
    email = (raw or "").strip().lower()
    if not email.endswith(SCHOOL_DOMAIN):
        raise ValueError(f"校园邮箱必须以 {SCHOOL_DOMAIN} 结尾")
    prefix = email[:-len(SCHOOL_DOMAIN)]
    if not prefix.isdigit():
        raise ValueError("邮箱前缀必须是纯数字学号")
    return email


def student_id_of(email: str) -> str:
    return email[:-len(SCHOOL_DOMAIN)]


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _smtp_config():
    return (os.environ.get("SMTP_HOST") or "", int(os.environ.get("SMTP_PORT") or "465"),
            os.environ.get("SMTP_USER") or "", os.environ.get("SMTP_PASSWORD") or "",
            os.environ.get("SMTP_FROM") or "寝室冰箱管理 <noreply@local>")


def configured() -> bool:
    host, _, user, pw, _ = _smtp_config()
    return bool(host and user and pw)


def send_mail(to: str, subject: str, body: str) -> None:
    host, port, user, pw, sender = _smtp_config()
    if not configured():
        print(f"[email][DEV] 未配置 SMTP，邮件不发送 -> {to} | {subject} | {body[:120]}")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    # envelope 发件人必须是授权邮箱本身（126 等服务商严格校验）
    from email.utils import parseaddr
    envelope_from = parseaddr(sender)[1] or user
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15) as s:
            s.login(user, pw)
            s.sendmail(envelope_from, [to], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=15) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(envelope_from, [to], msg.as_string())


def send_code(email: str, purpose: str) -> None:
    """生成验证码、落库（哈希）、发邮件。调用方需先做限流与业务前置校验。"""
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    now = datetime.now()
    db.conn().execute(
        "INSERT INTO email_codes(email,code_hash,purpose,expires_at) VALUES(?,?,?,?)",
        (email, _hash(code), purpose, (now + timedelta(minutes=CODE_TTL_MINUTES))
         .strftime("%Y-%m-%d %H:%M:%S")))
    label = {"register": "注册", "bind": "绑定邮箱", "reset": "重置密码"}.get(purpose, purpose)
    send_mail(email, f"寝室冰箱管理 - {label}验证码",
              f"您的{label}验证码是：{code}\n{CODE_TTL_MINUTES} 分钟内有效，请勿转发他人。")
    if not configured():
        print(f"[email][DEV] 验证码（{purpose}）{email}: {code}")


def can_send(email: str) -> tuple[bool, str]:
    """按邮箱做发送频率限制：60 秒冷却 + 每天上限。"""
    c = db.conn()
    last = c.execute("SELECT created_at FROM email_codes WHERE email=? "
                     "ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    if last:
        try:
            t = datetime.strptime(str(last["created_at"])[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            t = None
        if t and (datetime.now() - t).total_seconds() < SEND_COOLDOWN_SECONDS:
            return False, "发送太频繁，请稍后再试"
    today = datetime.now().strftime("%Y-%m-%d")
    n = c.execute("SELECT COUNT(*) FROM email_codes WHERE email=? AND created_at LIKE ?",
                  (email, today + "%")).fetchone()[0]
    if n >= SEND_DAILY_LIMIT:
        return False, "今日验证码发送次数已达上限"
    return True, ""


def verify_code(email: str, code: str, purpose: str) -> tuple[bool, str]:
    """校验验证码：有效期、用途、尝试次数、一次性。返回 (是否通过, 错误提示)。"""
    c = db.conn()
    row = c.execute("SELECT * FROM email_codes WHERE email=? AND purpose=? "
                    "AND used=0 ORDER BY id DESC LIMIT 1", (email, purpose)).fetchone()
    if not row:
        return False, "验证码不存在或已使用，请重新获取"
    try:
        exp = datetime.strptime(str(row["expires_at"])[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False, "验证码已失效"
    if datetime.now() > exp:
        return False, "验证码已过期"
    if row["attempts"] >= CODE_MAX_ATTEMPTS:
        return False, "尝试次数过多，请重新获取"
    if not secrets.compare_digest(_hash(code or ""), row["code_hash"]):
        c.execute("UPDATE email_codes SET attempts=attempts+1 WHERE id=?", (row["id"],))
        return False, "验证码错误"
    c.execute("UPDATE email_codes SET used=1 WHERE id=?", (row["id"],))
    return True, ""


def send_random_password(email: str, new_password: str) -> None:
    send_mail(email, "寝室冰箱管理 - 密码已重置",
              f"您的网站密码已由管理员重置为：\n{new_password}\n请登录后立即修改。")
