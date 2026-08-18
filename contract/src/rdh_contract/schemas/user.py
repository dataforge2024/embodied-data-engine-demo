"""用户与认证。

Platform 用 JWT + RBAC；:class:`Role` 对应工作区划分。
"""

from datetime import datetime

from pydantic import Field

from ..enums import Role
from .base import ContractModel


class User(ContractModel):
    """用户视图。

    绝不包含密码哈希等凭据字段 —— 该模型会经 API 返回给前端。
    """

    user_id: str = Field(description="用户 ID（UUID）")
    username: str = Field(min_length=1, max_length=64, description="登录名")
    display_name: str = Field(description="展示名")
    roles: tuple[Role, ...] = Field(description="角色列表，决定可访问的工作区")
    active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(description="创建时间（UTC）")

    def has_role(self, role: Role) -> bool:
        """是否具备指定角色。"""
        return role in self.roles


class TokenPayload(ContractModel):
    """JWT 载荷。

    字段名沿用 JWT 注册声明（``sub`` / ``exp`` / ``iat``），便于标准库直接解析。
    """

    sub: str = Field(description="用户 ID")
    roles: tuple[Role, ...] = Field(description="角色列表")
    exp: int = Field(description="过期时间（Unix 秒）")
    iat: int = Field(description="签发时间（Unix 秒）")
    jti: str | None = Field(default=None, description="Token ID，用于吊销")


class LoginRequest(ContractModel):
    """登录请求。"""

    username: str = Field(min_length=1, max_length=64, description="登录名")
    password: str = Field(min_length=1, description="密码，仅在传输中出现，不落任何日志")


class TokenResponse(ContractModel):
    """登录响应。"""

    access_token: str = Field(description="JWT")
    token_type: str = Field(default="bearer", description="Token 类型")
    expires_in: int = Field(gt=0, description="有效期（秒）")
    user: User = Field(description="当前用户")


__all__ = ["LoginRequest", "TokenPayload", "TokenResponse", "User"]
