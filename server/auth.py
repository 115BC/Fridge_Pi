"""登录令牌(JWT) 与 树莓派解锁令牌(HMAC) 的签发/校验，及角色依赖。"""
import hashlib
import hmac
import time
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException

import db

ALGO = "HS256"


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
        return dict(row)

    def require_role(self, *roles):
        def dep(user: dict = Depends(self.user)):
            if user["role"] not in roles:
                raise HTTPException(403, f"需要角色: {'/'.join(roles)}")
            return user
        return dep
