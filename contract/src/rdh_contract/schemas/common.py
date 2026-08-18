"""统一 API 响应封套与分页元数据。

对齐全局约定：响应包含 success 指示、data 负载（出错时为 null）、error 信息（成功时为 null），
分页响应额外带 meta。
"""

from pydantic import Field

from .base import ContractModel


class ErrorDetail(ContractModel):
    """错误详情。

    ``message`` 面向终端用户，不得包含堆栈、SQL、内部路径等敏感信息；
    详细上下文记录在服务端日志，用 ``trace_id`` 关联。
    """

    code: str = Field(description="机器可读错误码，如 EPISODE_NOT_FOUND")
    message: str = Field(description="面向用户的错误描述")
    field: str | None = Field(default=None, description="校验失败的字段名")
    trace_id: str | None = Field(default=None, description="关联服务端日志的追踪 ID")


class PageMeta(ContractModel):
    """分页元数据。"""

    total: int = Field(ge=0, description="符合条件的总记录数")
    page: int = Field(ge=1, description="当前页码，从 1 开始")
    limit: int = Field(ge=1, le=200, description="每页记录数")

    @property
    def total_pages(self) -> int:
        """总页数。"""
        return (self.total + self.limit - 1) // self.limit if self.limit else 0

    @property
    def has_next(self) -> bool:
        """是否有下一页。"""
        return self.page < self.total_pages


class ApiResponse[T](ContractModel):
    """统一响应封套。"""

    success: bool = Field(description="请求是否成功")
    data: T | None = Field(default=None, description="负载，出错时为 null")
    error: ErrorDetail | None = Field(default=None, description="错误信息，成功时为 null")


class PaginatedResponse[T](ContractModel):
    """分页响应封套。"""

    success: bool = Field(description="请求是否成功")
    data: list[T] = Field(default_factory=list, description="当前页记录")
    meta: PageMeta | None = Field(default=None, description="分页元数据")
    error: ErrorDetail | None = Field(default=None, description="错误信息，成功时为 null")


__all__ = ["ApiResponse", "ErrorDetail", "PageMeta", "PaginatedResponse"]
