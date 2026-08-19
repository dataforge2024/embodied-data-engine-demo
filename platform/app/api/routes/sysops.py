"""SysOps 主动操作路由（任务取消、回传触发）。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from rdh_contract.enums import Role
from rdh_contract.schemas import ApiResponse, ErrorDetail, User

from app.api.dependencies import require_roles
from app.ws.manager import get_connection_manager

router = APIRouter(prefix="/sysops", tags=["sysops"])


class TriggerUploadRequest(BaseModel):
    """触发回传请求体。"""

    agent_id: str = Field(description="目标 Agent ID")
    task_id: str | None = Field(default=None, description="限定任务，None 表示全部")
    reason: str | None = Field(default=None, description="触发原因")


@router.post("/trigger-upload", summary="催促 Agent 重扫并上传")
async def trigger_upload(
    request: TriggerUploadRequest,
    user: Annotated[User, Depends(require_roles(Role.ADMIN, Role.RECORDER))],
) -> ApiResponse[dict]:
    """发送 ``down.upload_trigger`` 帧给 Agent（admin 与 recorder 均可）。

    Agent 平时靠目录监听自动上传，本接口用于监听漏掉或上传失败后的人工补救。
    Agent 离线时返回 ``success: false``。
    """
    sent = await get_connection_manager().trigger_upload(
        request.agent_id, task_id=request.task_id, reason=request.reason
    )
    if not sent:
        return ApiResponse(
            success=False,
            data=None,
            error=ErrorDetail(code="AGENT_OFFLINE", message="Agent 离线，无法触发回传"),
        )
    return ApiResponse(success=True, data={"sent": True})


__all__ = ["router"]
