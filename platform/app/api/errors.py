"""统一错误响应。

对外只回 :class:`~rdh_contract.schemas.ErrorDetail`：机器可读 ``code`` + 面向用户的 ``message``。
异常细节记服务端日志，用 ``trace_id`` 关联 —— 不把堆栈、SQL、内部路径回给调用方。
"""

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rdh_contract.schemas import ApiResponse, ErrorDetail
from rdh_contract.state_machine import InvalidTransitionError

from app.core.security import AuthError
from app.services.callbacks import ChecksumMismatchError
from app.services.episode_lifecycle import EpisodeNotFoundError
from app.services.event_publisher import UnregisteredEventError

logger = logging.getLogger(__name__)


def error_response(
    *, status_code: int, code: str, message: str, trace_id: str | None = None
) -> JSONResponse:
    """构造统一错误响应。"""
    envelope: ApiResponse[Any] = ApiResponse(
        success=False,
        error=ErrorDetail(code=code, message=message, trace_id=trace_id),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    """挂载全局异常处理器。"""

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
        """认证失败 → 401。不回显具体原因（用户名是否存在等）。"""
        return error_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="UNAUTHORIZED",
            message=str(exc),
        )

    @app.exception_handler(EpisodeNotFoundError)
    async def _episode_missing(request: Request, exc: EpisodeNotFoundError) -> JSONResponse:
        """Episode 不存在 → 404。"""
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="EPISODE_NOT_FOUND",
            message="Episode 不存在",
        )

    @app.exception_handler(InvalidTransitionError)
    async def _invalid_transition(request: Request, exc: InvalidTransitionError) -> JSONResponse:
        """非法状态迁移 → 409。

        错误信息里的状态名是契约的公开词汇（TS 侧也有），不算内部细节泄露。
        """
        return error_response(
            status_code=status.HTTP_409_CONFLICT,
            code="INVALID_STATE_TRANSITION",
            message=str(exc),
        )

    @app.exception_handler(ChecksumMismatchError)
    async def _checksum_mismatch(request: Request, exc: ChecksumMismatchError) -> JSONResponse:
        """上传内容校验失败 → 422。"""
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="CHECKSUM_MISMATCH",
            message=str(exc),
        )

    @app.exception_handler(UnregisteredEventError)
    async def _unregistered_event(request: Request, exc: UnregisteredEventError) -> JSONResponse:
        """试图发布未注册事件 → 500（这是服务端 bug，不是调用方的错）。"""
        trace_id = str(uuid.uuid4())
        logger.error("发布未注册事件 trace_id=%s: %s", trace_id, exc)
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="EVENT_NOT_REGISTERED",
            message="服务端内部错误",
            trace_id=trace_id,
        )

    @app.exception_handler(KeyError)
    async def _key_error(request: Request, exc: KeyError) -> JSONResponse:
        """仓储层的「不存在」→ 404。"""
        return error_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="RESOURCE_NOT_FOUND",
            message="资源不存在",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """请求体校验失败 → 422，指出第一个出错字段。"""
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        envelope: ApiResponse[Any] = ApiResponse(
            success=False,
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                message=str(first.get("msg", "请求参数校验失败")),
                field=location or None,
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=envelope.model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """兜底：绝不把异常细节回给调用方。"""
        trace_id = str(uuid.uuid4())
        logger.exception("未处理异常 trace_id=%s path=%s", trace_id, request.url.path)
        return error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="服务端内部错误",
            trace_id=trace_id,
        )


__all__ = ["error_response", "register_exception_handlers"]
