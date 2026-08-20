"""Episode 状态流转历史表。

``actor_id`` 一列存两种语义（人工的 user_id / 系统的环节名），由 ``actor_type`` 区分。
拆两列会让每行必有一个是 NULL，而这两个值永远不同时出现；contract 侧的
:class:`~rdh_contract.schemas.TransitionActor` 仍然分成两个字段，转换在仓储里做。

不建到 ``episodes`` 的 relationship：查询一律按 ``episode_id`` 走，
async session 下的惰性加载只会带来 ``MissingGreenlet``。
"""

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class TransitionRow(Base):
    """一条状态流转记录。只追加，不更新。"""

    __tablename__ = "episode_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[str] = mapped_column(String(64), nullable=False)

    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)

    # "user" | "system"
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # user 时是 user_id，system 时是环节名（upload_callback / scheduler / …）
    actor_id: Mapped[str | None] = mapped_column(String(64))

    reason: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        # 轨迹查询：按 episode 捞全部记录并按时间排序
        Index("ix_transitions_episode_time", "episode_id", "occurred_at"),
    )


__all__ = ["TransitionRow"]
