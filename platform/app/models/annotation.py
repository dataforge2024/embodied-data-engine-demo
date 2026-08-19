"""标注记录表。

一个 Episode 对应一条标注记录；退回重做时不新建行，而是 ``revision`` +1 并覆盖 segments。
保留历史版本是后续需求，届时加 ``annotation_revisions`` 表，不改本表结构。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class AnnotationRow(Base):
    """标注记录持久化行。"""

    __tablename__ = "annotations"

    annotation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    segments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    verify_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    review_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    annotated_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("ix_annotations_episode", "episode_id"),)


__all__ = ["AnnotationRow"]
