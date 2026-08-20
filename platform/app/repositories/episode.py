"""Episode 仓储。

**不暴露裸的 status 赋值**：状态变更只能经 :meth:`EpisodeRepository.apply_transition`，
而调用它的唯一合法入口是 ``services/episode_lifecycle.py``（它先过 contract 的守卫）。
这是 openspec/project.md 里「Episode 状态变更唯一负责方」约束的落地点。
"""

from datetime import UTC, datetime
from typing import Any

from rdh_contract.enums import EpisodeStatus
from rdh_contract.schemas import (
    Episode,
    KeyFrame,
    QualityReport,
    Segment,
    SensorStream,
    TransitionActor,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.episode import EpisodeRow
from app.repositories.transition import TransitionRepository


def row_to_episode(row: EpisodeRow) -> Episode:
    """ORM 行 → contract 模型。"""
    return Episode(
        episode_id=row.episode_id,
        task_id=row.task_id,
        agent_id=row.agent_id,
        recorded_by=row.recorded_by,
        status=EpisodeStatus(row.status),
        object_key=row.object_key,
        size_bytes=row.size_bytes,
        duration_ms=row.duration_ms,
        checksum=row.checksum,
        streams=tuple(SensorStream(**s) for s in row.streams),
        key_frames=tuple(KeyFrame(**k) for k in row.key_frames),
        segments=tuple(Segment(**s) for s in row.segments),
        quality=QualityReport(**row.quality) if row.quality else None,
        robot_model=row.robot_model,
        scene=row.scene,
        reject_reason=row.reject_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class EpisodeRepository:
    """Episode 数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        episode_id: str,
        task_id: str,
        agent_id: str,
        status: EpisodeStatus,
        recorded_by: str | None = None,
        robot_model: str | None = None,
        scene: str | None = None,
    ) -> Episode:
        """新建 Episode。初始状态由调用方给出（应为 ``RECORDING``）。"""
        now = datetime.now(UTC)
        row = EpisodeRow(
            episode_id=episode_id,
            task_id=task_id,
            agent_id=agent_id,
            recorded_by=recorded_by,
            status=status.value,
            streams=[],
            key_frames=[],
            segments=[],
            robot_model=robot_model,
            scene=scene,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_episode(row)

    async def find_by_id(self, episode_id: str) -> Episode | None:
        """按 ID 查询，不存在返回 None。"""
        row = await self._session.get(EpisodeRow, episode_id)
        return row_to_episode(row) if row else None

    async def find_all(
        self,
        *,
        status: EpisodeStatus | None = None,
        task_id: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[tuple[Episode, ...], int]:
        """分页查询，返回 ``(记录, 总数)``。按创建时间正序保证队列 FIFO。"""
        conditions = []
        if status is not None:
            conditions.append(EpisodeRow.status == status.value)
        if task_id is not None:
            conditions.append(EpisodeRow.task_id == task_id)

        count_stmt = select(func.count()).select_from(EpisodeRow)
        list_stmt = select(EpisodeRow).order_by(EpisodeRow.created_at)
        for condition in conditions:
            count_stmt = count_stmt.where(condition)
            list_stmt = list_stmt.where(condition)

        total = (await self._session.execute(count_stmt)).scalar_one()
        rows = (
            (await self._session.execute(list_stmt.offset((page - 1) * limit).limit(limit)))
            .scalars()
            .all()
        )
        return tuple(row_to_episode(r) for r in rows), total

    async def apply_transition(
        self,
        episode_id: str,
        *,
        target: EpisodeStatus,
        actor: TransitionActor,
        reject_reason: str | None = None,
    ) -> tuple[Episode, bool]:
        """写入新状态并记录流转。

        **调用前必须已过 contract 守卫** —— 本方法不做合法性判断，
        唯一合法调用方是 ``services/episode_lifecycle.py``。

        Returns:
            (更新后的 Episode, changed) —— changed=False 表示目标状态已达成（幂等重放）
        """
        row = await self._require_row(episode_id)
        from_status = EpisodeStatus(row.status)

        # 幂等：目标状态已达成，不重复记录
        if from_status == target:
            return row_to_episode(row), False

        row.status = target.value
        if reject_reason is not None:
            row.reject_reason = reject_reason
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

        # 轨迹记录收口在此：状态变不了而不经过本方法，所以这里不会漏记
        # （design.md 第 7 节）。重放已在上面返回，非法迁移被 lifecycle 的守卫拦在外面。
        await TransitionRepository(self._session).record(
            episode_id=episode_id,
            from_status=from_status,
            to_status=target,
            actor=actor,
            reason=reject_reason,
        )

        return row_to_episode(row), True

    async def attach_upload_result(
        self,
        episode_id: str,
        *,
        object_key: str,
        size_bytes: int,
        checksum: str,
        duration_ms: int,
    ) -> Episode:
        """记录上传产物元信息（交互③）。"""
        row = await self._require_row(episode_id)
        row.object_key = object_key
        row.size_bytes = size_bytes
        row.checksum = checksum
        row.duration_ms = duration_ms
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_episode(row)

    async def attach_processing_result(
        self,
        episode_id: str,
        *,
        streams: tuple[SensorStream, ...] | None = None,
        key_frames: tuple[KeyFrame, ...] | None = None,
        segments: tuple[Segment, ...] | None = None,
        quality: QualityReport | None = None,
    ) -> Episode:
        """写入算子产物（交互⑧）。传 None 的字段保持原值。"""
        row = await self._require_row(episode_id)
        if streams is not None:
            row.streams = [s.model_dump(mode="json") for s in streams]
        if key_frames is not None:
            row.key_frames = [k.model_dump(mode="json") for k in key_frames]
        if segments is not None:
            row.segments = [s.model_dump(mode="json") for s in segments]
        if quality is not None:
            row.quality = quality.model_dump(mode="json")
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_episode(row)

    async def replace_segments(self, episode_id: str, segments: tuple[Segment, ...]) -> Episode:
        """整体替换分段（标注提交，全量而非增量）。"""
        row = await self._require_row(episode_id)
        row.segments = [s.model_dump(mode="json") for s in segments]
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_episode(row)

    async def count_by_status(self) -> dict[str, int]:
        """按状态统计，SysOps 看板用。"""
        stmt = select(EpisodeRow.status, func.count()).group_by(EpisodeRow.status)
        rows: Any = (await self._session.execute(stmt)).all()
        return {status: count for status, count in rows}

    async def update_upload_progress(
        self, episode_id: str, *, uploaded_parts: int, total_parts: int
    ) -> None:
        """更新上传进度（节流器调用）。

        格式：``{"uploaded_parts": 10, "total_parts": 50, "updated_at": "2026-08-19T..."}``
        """
        row = await self._require_row(episode_id)
        row.upload_progress = {
            "uploaded_parts": uploaded_parts,
            "total_parts": total_parts,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        row.updated_at = datetime.now(UTC)
        await self._session.flush()

    async def _require_row(self, episode_id: str) -> EpisodeRow:
        """取行，不存在抛 KeyError（上层转 404）。"""
        row = await self._session.get(EpisodeRow, episode_id)
        if row is None:
            raise KeyError(f"Episode 不存在：{episode_id}")
        return row


__all__ = ["EpisodeRepository", "row_to_episode"]
