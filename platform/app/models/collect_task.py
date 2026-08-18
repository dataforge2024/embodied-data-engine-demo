"""采集任务表。"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CollectTaskRow(Base):
    """采集任务持久化行。"""

    __tablename__ = "collect_tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    # 采集要求整体存 JSON：字段随业务演进（新增传感器要求等），拆列会频繁迁移
    requirement: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    assignments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)

    collected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_tasks_status", "status"),)


__all__ = ["CollectTaskRow"]
