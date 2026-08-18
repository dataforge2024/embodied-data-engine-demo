"""认证与授权原语。

JWT 用 HMAC-SHA256 手工签发，避免为 demo 引入 python-jose/pyjwt。算法固定在服务端，
不从 token header 读取——防止 alg 混淆攻击。
"""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from rdh_contract.enums import Role
from rdh_contract.schemas import TokenPayload

ALGORITHM = "HS256"

# 密码哈希迭代次数。生产应改用 bcrypt/argon2；此处用 PBKDF2 避免额外依赖。
PBKDF2_ITERATIONS = 240_000


class AuthError(Exception):
    """认证失败。调用方应转成 401，且不回显具体原因。"""


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 编码，去掉填充。"""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """URL-safe base64 解码，补回填充。"""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, *, salt: str | None = None) -> str:
    """生成 ``pbkdf2_sha256$<iterations>$<salt>$<hash>`` 格式的密码哈希。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。使用常量时间比较，避免时序侧信道。"""
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), expected)


def create_access_token(
    *, user_id: str, roles: tuple[Role, ...], secret: str, ttl_seconds: int
) -> str:
    """签发 JWT。"""
    issued_at = int(time.time())
    header = {"alg": ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "roles": [role.value for role in roles],
        "iat": issued_at,
        "exp": issued_at + ttl_seconds,
        "jti": secrets.token_urlsafe(12),
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str, *, secret: str) -> TokenPayload:
    """校验并解析 JWT，失败抛 :class:`AuthError`。"""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        raise AuthError("token 格式非法") from None

    signing_input = f"{header_b64}.{payload_b64}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_decode(signature_b64), expected):
        raise AuthError("签名校验失败")

    try:
        raw: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        raise AuthError("载荷解析失败") from None

    if int(raw.get("exp", 0)) < time.time():
        raise AuthError("token 已过期")

    try:
        return TokenPayload(
            sub=str(raw["sub"]),
            roles=tuple(Role(r) for r in raw.get("roles", [])),
            exp=int(raw["exp"]),
            iat=int(raw["iat"]),
            jti=raw.get("jti"),
        )
    except (KeyError, ValueError):
        raise AuthError("载荷字段非法") from None


def verify_service_token(provided: str | None, expected: str) -> bool:
    """校验内部服务凭据（Agent / Scheduler 回调）。常量时间比较。"""
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


__all__ = [
    "ALGORITHM",
    "AuthError",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "verify_password",
    "verify_service_token",
]
