"""训练集构建路由（Lab 工作区）。

构建是**异步**的：本端点只发布 ``dataset.build_requested`` 事件并回 202，
实际的格式转换与打包由 Scheduler tool-worker 完成。同步构建会让请求挂几分钟。
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from rdh_contract.enums import Role
from rdh_contract.events import DatasetBuildRequested
from rdh_contract.schemas import ApiResponse, Dataset, User

from app.api.dependencies import (
    CurrentUserDep,
    DatasetRepoDep,
    PublisherDep,
    SessionDep,
    require_roles,
)

router = APIRouter(prefix="/datasets", tags=["datasets"])

# 支持的导出格式。收窄取值避免下游 worker 拿到无法处理的格式
SUPPORTED_FORMATS = frozenset({"lerobot", "rlds"})


class BuildRequest(BaseModel):
    """构建请求。"""

    episode_ids: list[str] = Field(min_length=1, description="纳入的 Episode")
    output_format: str = Field(description="导出格式：lerobot / rlds")


class BuildAccepted(BaseModel):
    """已受理响应。"""

    dataset_id: str = Field(description="训练集 ID，用于后续查询构建状态")
    episode_count: int = Field(description="纳入的 Episode 数量")


@router.post("/build", status_code=202, summary="请求构建训练集")
async def build_dataset(
    request: BuildRequest,
    publisher: PublisherDep,
    datasets: DatasetRepoDep,
    session: SessionDep,
    user: Annotated[User, Depends(require_roles(Role.LAB))],
) -> ApiResponse[BuildAccepted]:
    """发布构建事件，由 Scheduler tool-worker 异步处理。

    格式不受支持时报 422 —— 与其让 worker 拿到未知格式后失败，不如在入口拒绝。
    """
    if request.output_format not in SUPPORTED_FORMATS:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=f"不支持的导出格式：{request.output_format}；"
            f"可选 {', '.join(sorted(SUPPORTED_FORMATS))}",
        )

    dataset_id = str(uuid.uuid4())

    # 先落库再发事件：与 episode_lifecycle 同一条纪律。反过来 worker 可能消费到
    # Platform 还查不到的 dataset，而调用方拿到 dataset_id 后立刻 GET 会撞 404。
    await datasets.create(
        dataset_id=dataset_id,
        episode_ids=tuple(request.episode_ids),
        output_format=request.output_format,
        requested_by=user.user_id,
    )
    await publisher.publish(
        "dataset.build_requested",
        DatasetBuildRequested(
            event_id=str(uuid.uuid4()),
            occurred_at=datetime.now(UTC),
            dataset_id=dataset_id,
            episode_ids=tuple(request.episode_ids),
            output_format=request.output_format,
            requested_by=user.user_id,
        ),
    )
    await session.commit()
    return ApiResponse(
        success=True,
        data=BuildAccepted(dataset_id=dataset_id, episode_count=len(request.episode_ids)),
    )


@router.get("/{dataset_id}", summary="获取训练集构建状态")
async def get_dataset(
    dataset_id: str,
    datasets: DatasetRepoDep,
    user: CurrentUserDep,
) -> ApiResponse[Dataset]:
    """查询构建状态与产物位置。

    ``status=succeeded`` 时 ``manifest_key`` 才有值。本阶段 tool-worker 仍是 stub，
    所以查到的都是 ``pending``（tasks.md 8.4-8.6 落地后才会推进）。
    """
    dataset = await datasets.find_by_id(dataset_id)
    if dataset is None:
        raise KeyError(dataset_id)
    return ApiResponse(success=True, data=dataset)


__all__ = ["router"]
