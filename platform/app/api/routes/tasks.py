"""采集任务路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from rdh_contract.enums import Role
from rdh_contract.schemas import (
    ApiResponse,
    CollectTask,
    PageMeta,
    PaginatedResponse,
    TaskAssignment,
    TaskCreate,
    User,
)

from app.api.dependencies import (
    CurrentUserDep,
    SessionDep,
    TaskRepoDep,
    TaskServiceDep,
    require_roles,
)
from app.ws.manager import get_connection_manager

router = APIRouter(prefix="/tasks", tags=["tasks"])


class AssignRequest(BaseModel):
    """分派请求体。"""

    agent_id: str = Field(description="目标 Agent ID")


@router.get("", summary="分页查询采集任务")
async def list_tasks(
    tasks: TaskRepoDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedResponse[CollectTask]:
    """分页查询，按创建时间倒序。"""
    records, total = await tasks.find_all(page=page, limit=limit)
    return PaginatedResponse(
        success=True, data=list(records), meta=PageMeta(total=total, page=page, limit=limit)
    )


@router.post("", status_code=201, summary="创建采集任务")
async def create_task(
    payload: TaskCreate,
    service: TaskServiceDep,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(Role.ADMIN))],
) -> ApiResponse[CollectTask]:
    """创建采集任务（需 admin）。初始状态 ``draft``。"""
    task = await service.create_task(payload, created_by=user.user_id)
    await session.commit()
    return ApiResponse(success=True, data=task)


@router.get("/{task_id}", summary="获取单个任务")
async def get_task(
    task_id: str, tasks: TaskRepoDep, user: CurrentUserDep
) -> ApiResponse[CollectTask]:
    """按 ID 查询。"""
    task = await tasks.find_by_id(task_id)
    if task is None:
        raise KeyError(task_id)
    return ApiResponse(success=True, data=task)


@router.post("/{task_id}/assign", summary="把任务分派给 Agent")
async def assign_task(
    task_id: str,
    request: AssignRequest,
    service: TaskServiceDep,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(Role.ADMIN))],
) -> ApiResponse[TaskAssignment]:
    """分派任务，并经 WebSocket 推送给 Agent（交互①）。

    推送失败不回滚分派 —— Agent 重连后会拉取已分派任务。
    """
    task, assignment = await service.assign(
        task_id, agent_id=request.agent_id, assigned_by=user.user_id
    )
    await session.commit()

    await get_connection_manager().push_task(
        request.agent_id, task_id=task.task_id, task_name=task.name, requirement=task.requirement
    )
    return ApiResponse(success=True, data=assignment)


__all__ = ["router"]
