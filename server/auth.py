"""登录令牌(JWT) 与 树莓派解锁令牌(HMAC) 的签发/校验，及角色依赖。"""
import hashlib
import hmac
import os
import time
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException

import db

ALGO = "HS256"
PBKDF2_ITERS = 120000


def hash_password(password: str) -> str:
    """网站账号密码：pbkdf2_hmac-sha256 + 随机盐，存 'pbkdf2$iterations$salt$hash'。"""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERS)
    return f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, hash_hex = (stored or "").split("$")
        if scheme != "pbkdf2":
            return False
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                     bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def public_user(row: dict) -> dict:
    """对外输出用户时剔除密码哈希，绝不回传前端。"""
    return {k: v for k, v in row.items() if k != "password_hash"}


def make_jwt(openid: str, role: str, secret: str, expire_hours: int) -> str:
    now = int(time.time())
    payload = {"openid": openid, "role": role, "iat": now, "exp": now + expire_hours * 3600}
    return jwt.encode(payload, secret, algorithm=ALGO)


def decode_jwt(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(401, "登录令牌无效")


def sign_unlock(code: str, ts: int, pi_secret: str) -> str:
    return hmac.new(pi_secret.encode(), f"{code}|{ts}".encode(), hashlib.sha256).hexdigest()


def check_pi_secret(provided: Optional[str], expected: str):
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "树莓派密钥无效")


class Auth:
    """把 JWT 配置绑定到 FastAPI 依赖。"""

    def __init__(self, jwt_secret: str):
        self.jwt_secret = jwt_secret

    def user(self, authorization: Optional[str] = Header(None)) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "未登录")
        payload = decode_jwt(authorization[7:], self.jwt_secret)
        row = db.conn().execute(
            "SELECT * FROM users WHERE openid=?", (payload["openid"],)).fetchone()
        if not row:
            raise HTTPException(401, "用户不存在")
        return public_user(dict(row))

    def require_role(self, *roles):
        def dep(user: dict = Depends(self.user)):
            if user["role"] not in roles:
                raise HTTPException(403, f"需要角色: {'/'.join(roles)}")
            return user
        return dep
