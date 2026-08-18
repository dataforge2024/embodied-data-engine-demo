"""Agent 节点仓储。

在线状态实时算出而非落库：``online = now - last_heartbeat_at < timeout``。
"""

from datetime import UTC, datetime, timedelta

from rdh_contract.schemas import AgentHeartbeat, AgentNode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_node import AgentNodeRow


def row_to_agent_node(row: AgentNodeRow, *, timeout_seconds: int) -> AgentNode:
    """ORM 行 → contract 模型，在线状态由心跳时间推算。"""
    online = False
    if row.last_heartbeat_at is not None:
        last = row.last_heartbeat_at
        if last.tzinfo is None:  # SQLite 取回可能丢时区
            last = last.replace(tzinfo=UTC)
        online = datetime.now(UTC) - last < timedelta(seconds=timeout_seconds)
    return AgentNode(
        agent_id=row.agent_id,
        hostname=row.hostname,
        version=row.version,
        online=online,
        last_heartbeat=AgentHeartbeat(**row.last_heartbeat) if row.last_heartbeat else None,
        assigned_task_ids=tuple(row.assigned_task_ids),
        registered_at=row.registered_at,
    )


class AgentNodeRepository:
    """Agent 节点数据访问。"""

    def __init__(self, session: AsyncSession, *, heartbeat_timeout_seconds: int) -> None:
        self._session = session
        self._timeout = heartbeat_timeout_seconds

    async def register(self, *, agent_id: str, hostname: str, version: str) -> AgentNode:
        """注册或更新 Agent（重连时复用同一行）。"""
        row = await self._session.get(AgentNodeRow, agent_id)
        if row is None:
            row = AgentNodeRow(
                agent_id=agent_id,
                hostname=hostname,
                version=version,
                assigned_task_ids=[],
                registered_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            row.hostname = hostname
            row.version = version
        await self._session.flush()
        return row_to_agent_node(row, timeout_seconds=self._timeout)

    async def record_heartbeat(self, heartbeat: AgentHeartbeat) -> AgentNode:
        """记录心跳。"""
        row = await self._session.get(AgentNodeRow, heartbeat.agent_id)
        if row is None:
            raise KeyError(f"Agent 未注册：{heartbeat.agent_id}")
        row.last_heartbeat = heartbeat.model_dump(mode="json")
        row.last_heartbeat_at = heartbeat.reported_at
        await self._session.flush()
        return row_to_agent_node(row, timeout_seconds=self._timeout)

    async def assign_task(self, agent_id: str, task_id: str) -> AgentNode:
        """把任务挂到 Agent 上。"""
        row = await self._session.get(AgentNodeRow, agent_id)
        if row is None:
            raise KeyError(f"Agent 未注册：{agent_id}")
        if task_id not in row.assigned_task_ids:
            row.assigned_task_ids = [*row.assigned_task_ids, task_id]
        await self._session.flush()
        return row_to_agent_node(row, timeout_seconds=self._timeout)

    async def find_all(self) -> tuple[AgentNode, ...]:
        """列出全部 Agent。"""
        rows = (
            (await self._session.execute(select(AgentNodeRow).order_by(AgentNodeRow.agent_id)))
            .scalars()
            .all()
        )
        return tuple(row_to_agent_node(r, timeout_seconds=self._timeout) for r in rows)


__all__ = ["AgentNodeRepository", "row_to_agent_node"]
