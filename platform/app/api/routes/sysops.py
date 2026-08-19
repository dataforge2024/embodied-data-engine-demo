"""SysOps 主动操作路由（回传触发、队列巡检）。"""

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from rdh_contract.enums import Role
from rdh_contract.schemas import ApiResponse, ErrorDetail, User

from app.api.dependencies import SettingsDep, require_roles
from app.services.queue_inspector import inspect_file_queues, inspect_rabbit_queues
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


@router.get("/queues", summary="队列巡检：深度、死信、绑定")
async def queues(
    settings: SettingsDep,
    user: Annotated[User, Depends(require_roles(Role.ADMIN, Role.RECORDER))],
) -> ApiResponse[dict[str, Any]]:
    """按当前 ``RDH_QUEUE_BACKEND`` 巡检队列。

    **只读**：用被动声明查深度，不会顺手创建队列。broker 不可达时返回
    ``success: true`` 但 payload 里带 ``error`` —— 「连不上 broker」是运维页要显示的
    状态之一，不是接口故障。
    """
    if settings.uses_rabbit:
        snapshot = await inspect_rabbit_queues(
            amqp_url=settings.amqp_url, broker_label=settings.amqp_url_safe
        )
    else:
        snapshot = inspect_file_queues(
            queue_dir=settings.event_queue_dir, dlq_dir=settings.dlq_dir
        )
    return ApiResponse(success=True, data=asdict(snapshot))


__all__ = ["router"]
