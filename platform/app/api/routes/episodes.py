"""Episode 查询与录制上报。"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query
from rdh_contract.enums import EpisodeStatus
from rdh_contract.schemas import ApiResponse, Episode, EpisodeCreate, PageMeta, PaginatedResponse
from rdh_contract.state_machine import EPISODE_TRANSITIONS, INITIAL_STATE, TERMINAL_STATES

from app.api.dependencies import CurrentUserDep, EpisodeRepoDep, LifecycleDep, SessionDep

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.get("", summary="分页查询 Episode")
async def list_episodes(
    episodes: EpisodeRepoDep,
    user: CurrentUserDep,
    status: Annotated[EpisodeStatus | None, Query(description="按状态过滤")] = None,
    task_id: Annotated[str | None, Query(description="按任务过滤")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedResponse[Episode]:
    """分页查询，按创建时间正序（队列 FIFO）。"""
    records, total = await episodes.find_all(status=status, task_id=task_id, page=page, limit=limit)
    return PaginatedResponse(
        success=True,
        data=list(records),
        meta=PageMeta(total=total, page=page, limit=limit),
    )


@router.get("/stats", summary="按状态统计 Episode")
async def episode_stats(
    episodes: EpisodeRepoDep, user: CurrentUserDep
) -> ApiResponse[dict[str, int]]:
    """SysOps 看板：各状态的 Episode 数量。"""
    return ApiResponse(success=True, data=await episodes.count_by_status())


@router.get("/state-machine", summary="导出 Episode 状态机")
async def state_machine(user: CurrentUserDep) -> ApiResponse[dict[str, object]]:
    """暴露状态机给前端。

    前端据此禁用非法操作按钮，而不是自己硬编码一份状态规则
    （TS 侧也有生成的 `contract.ts`，此端点用于运行期校验版本一致）。
    """
    return ApiResponse(
        success=True,
        data={
            "initial": INITIAL_STATE.value,
            "terminal": sorted(s.value for s in TERMINAL_STATES),
            "transitions": {
                source.value: sorted(t.value for t in targets)
                for source, targets in EPISODE_TRANSITIONS.items()
            },
        },
    )


@router.post("", status_code=201, summary="开始录制（Agent 上报）")
async def create_episode(
    payload: EpisodeCreate,
    episodes: EpisodeRepoDep,
    session: SessionDep,
    user: CurrentUserDep,
) -> ApiResponse[Episode]:
    """创建 Episode，初始状态为 ``recording``。

    ``recorded_by`` 取自调用方 JWT 而非 payload —— 采集员身份由 Platform 认定，
    不采信 Agent 上报，避免 Agent 冒名。
    """
    episode = await episodes.create(
        episode_id=str(uuid.uuid4()),
        task_id=payload.task_id,
        agent_id=payload.agent_id,
        status=INITIAL_STATE,
        recorded_by=user.user_id,
        robot_model=payload.robot_model,
        scene=payload.scene,
    )
    await session.commit()
    return ApiResponse(success=True, data=episode)


@router.get("/{episode_id}", summary="获取单个 Episode")
async def get_episode(
    episode_id: str, episodes: EpisodeRepoDep, user: CurrentUserDep
) -> ApiResponse[Episode]:
    """按 ID 查询，不存在返回 404。"""
    episode = await episodes.find_by_id(episode_id)
    if episode is None:
        raise KeyError(episode_id)
    return ApiResponse(success=True, data=episode)


@router.post("/{episode_id}/start-upload", summary="录制结束，开始上传")
async def start_upload(
    episode_id: str,
    lifecycle: LifecycleDep,
    session: SessionDep,
    user: CurrentUserDep,
) -> ApiResponse[Episode]:
    """``recording → uploading``。由 Agent 在录制结束时调用。"""
    outcome = await lifecycle.transition(episode_id, target=EpisodeStatus.UPLOADING)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


__all__ = ["router"]
