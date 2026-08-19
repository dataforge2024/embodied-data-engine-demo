"""Agent 节点表。

在线状态不落库——由最近心跳时间与超时阈值实时算出，避免进程重启后残留「假在线」。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class AgentNodeRow(Base):
    """Agent 节点持久化行。"""

    __tablename__ = "agent_nodes"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)

    last_heartbeat: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    assigned_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    registered_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


__all__ = ["AgentNodeRow"]
