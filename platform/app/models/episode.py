"""Episode 表。

``status`` 存字符串（contract 的 :class:`~rdh_contract.enums.EpisodeStatus` 值），
不用数据库枚举——加状态时不必改 DDL，状态机的权威定义在 contract。

``streams`` / ``key_frames`` / ``segments`` 存 JSON：它们是随算子演进的半结构化数据，
拆表会让每次契约变更都牵动迁移。查询只按 episode_id / status / task_id 走，不需要这些字段的索引。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EpisodeRow(Base):
    """Episode 持久化行。"""

    __tablename__ = "episodes"

    episode_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    object_key: Mapped[str | None] = mapped_column(String(512))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(128))

    streams: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    key_frames: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    quality: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    robot_model: Mapped[str | None] = mapped_column(String(128))
    scene: Mapped[str | None] = mapped_column(String(128))
    reject_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 队列查询：按状态捞待核验/待标注，按 (status, created_at) 保证 FIFO
        Index("ix_episodes_status_created", "status", "created_at"),
        Index("ix_episodes_task", "task_id"),
    )


__all__ = ["EpisodeRow"]
