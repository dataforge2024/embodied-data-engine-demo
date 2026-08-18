"""认证服务。"""

import uuid

from rdh_contract.enums import Role
from rdh_contract.schemas import LoginRequest, TokenResponse, User

from app.core.security import (
    AuthError,
    create_access_token,
    hash_password,
    verify_password,
)
from app.repositories.user import UserRepository


class AuthService:
    """登录与用户创建。"""

    def __init__(self, *, users: UserRepository, jwt_secret: str, jwt_ttl_seconds: int) -> None:
        self._users = users
        self._secret = jwt_secret
        self._ttl = jwt_ttl_seconds

    async def login(self, request: LoginRequest) -> TokenResponse:
        """校验凭据并签发 JWT。

        用户不存在与密码错误返回同一个错误 —— 不泄露用户名是否存在。
        """
        found = await self._users.get_credentials(request.username)
        if found is None:
            raise AuthError("用户名或密码错误")

        user, password_hash = found
        if not verify_password(request.password, password_hash):
            raise AuthError("用户名或密码错误")
        if not user.active:
            raise AuthError("账号已停用")

        token = create_access_token(
            user_id=user.user_id,
            roles=user.roles,
            secret=self._secret,
            ttl_seconds=self._ttl,
        )
        return TokenResponse(access_token=token, expires_in=self._ttl, user=user)

    async def create_user(
        self, *, username: str, display_name: str, password: str, roles: tuple[Role, ...]
    ) -> User:
        """创建用户。密码在此处哈希，明文不落库、不进日志。"""
        return await self._users.create(
            user_id=str(uuid.uuid4()),
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            roles=roles,
        )


__all__ = ["AuthService"]
