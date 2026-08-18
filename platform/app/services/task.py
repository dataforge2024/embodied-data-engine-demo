"""采集任务编排。"""

import uuid
from datetime import UTC, datetime

from rdh_contract.schemas import CollectTask, TaskAssignment, TaskCreate

from app.repositories.agent_node import AgentNodeRepository
from app.repositories.task import TaskRepository


class TaskService:
    """任务创建与分派。"""

    def __init__(self, *, tasks: TaskRepository, agents: AgentNodeRepository) -> None:
        self._tasks = tasks
        self._agents = agents

    async def create_task(self, payload: TaskCreate, *, created_by: str) -> CollectTask:
        """创建采集任务。"""
        return await self._tasks.create(
            task_id=str(uuid.uuid4()), payload=payload, created_by=created_by
        )

    async def assign(
        self, task_id: str, *, agent_id: str, assigned_by: str
    ) -> tuple[CollectTask, TaskAssignment]:
        """把任务分派给 Agent。

        任务与 Agent 两侧都记录关联：任务侧留分派轨迹，Agent 侧供 SysOps 看单机负载。
        实际的 WS 推送（交互①）由路由层在分派成功后触发。
        """
        task = await self._tasks.find_by_id(task_id)
        if task is None:
            raise KeyError(f"任务不存在：{task_id}")

        assignment = TaskAssignment(
            task_id=task_id,
            agent_id=agent_id,
            assigned_by=assigned_by,
            assigned_at=datetime.now(UTC),
        )
        updated = await self._tasks.add_assignment(task_id, assignment)
        await self._agents.assign_task(agent_id, task_id)
        return updated, assignment


__all__ = ["TaskService"]
