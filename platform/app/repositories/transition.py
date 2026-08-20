"""Episode 状态流转历史仓储。

只追加，不更新也不删除 —— 轨迹是既成事实。POC 阶段没有归档策略
（design.md 第 7 节），每条 Episode 约 10 条记录，量不大。
"""

from datetime import UTC, datetime

from rdh_contract.enums import EpisodeStatus
from rdh_contract.schemas import TransitionActor, TransitionRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transition import TransitionRow


def row_to_record(row: TransitionRow) -> TransitionRecord:
    """ORM 行 → contract 模型。

    ``actor_id`` 单列存两种语义，按 ``actor_type`` 分派回对应字段。
    """
    is_user = row.actor_type == "user"
    return TransitionRecord(
        episode_id=row.episode_id,
        from_status=EpisodeStatus(row.from_status),
        to_status=EpisodeStatus(row.to_status),
        actor=TransitionActor(
            actor_type="user" if is_user else "system",
            user_id=row.actor_id if is_user else None,
            system_component=None if is_user else row.actor_id,
        ),
        reason=row.reason,
        occurred_at=row.occurred_at,
    )


class TransitionRepository:
    """状态流转历史数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        episode_id: str,
        from_status: EpisodeStatus,
        to_status: EpisodeStatus,
        actor: TransitionActor,
        reason: str | None = None,
    ) -> TransitionRecord:
        """追加一条流转记录。

        调用方保证状态真的变了 —— 幂等重放不该走到这里。
        """
        row = TransitionRow(
            episode_id=episode_id,
            from_status=from_status.value,
            to_status=to_status.value,
            actor_type=actor.actor_type,
            actor_id=actor.user_id or actor.system_component,
            reason=reason,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_record(row)

    async def get_history(self, episode_id: str) -> tuple[TransitionRecord, ...]:
        """按时间正序返回轨迹。相邻两条的时间差即前一状态的停留时长。"""
        stmt = (
            select(TransitionRow)
            .where(TransitionRow.episode_id == episode_id)
            .order_by(TransitionRow.occurred_at, TransitionRow.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(row_to_record(r) for r in rows)


__all__ = ["TransitionRepository", "row_to_record"]
