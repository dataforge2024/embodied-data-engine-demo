"""采集任务仓储。"""

from datetime import UTC, datetime

from rdh_contract.enums import TaskStatus
from rdh_contract.schemas import CollectTask, TaskAssignment, TaskCreate, TaskRequirement
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collect_task import CollectTaskRow


def row_to_task(row: CollectTaskRow) -> CollectTask:
    """ORM 行 → contract 模型。"""
    return CollectTask(
        task_id=row.task_id,
        name=row.name,
        description=row.description,
        status=TaskStatus(row.status),
        requirement=TaskRequirement(**row.requirement),
        collected_count=row.collected_count,
        published_count=row.published_count,
        assignments=tuple(TaskAssignment(**a) for a in row.assignments),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class TaskRepository:
    """采集任务数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, task_id: str, payload: TaskCreate, created_by: str) -> CollectTask:
        """新建任务，初始为 ``DRAFT``。"""
        now = datetime.now(UTC)
        row = CollectTaskRow(
            task_id=task_id,
            name=payload.name,
            description=payload.description,
            status=TaskStatus.DRAFT.value,
            requirement=payload.requirement.model_dump(mode="json"),
            assignments=[],
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_task(row)

    async def find_by_id(self, task_id: str) -> CollectTask | None:
        """按 ID 查询。"""
        row = await self._session.get(CollectTaskRow, task_id)
        return row_to_task(row) if row else None

    async def find_all(
        self, *, page: int = 1, limit: int = 20
    ) -> tuple[tuple[CollectTask, ...], int]:
        """分页查询，返回 ``(记录, 总数)``。"""
        total = (
            await self._session.execute(select(func.count()).select_from(CollectTaskRow))
        ).scalar_one()
        rows = (
            (
                await self._session.execute(
                    select(CollectTaskRow)
                    .order_by(CollectTaskRow.created_at.desc())
                    .offset((page - 1) * limit)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return tuple(row_to_task(r) for r in rows), total

    async def add_assignment(self, task_id: str, assignment: TaskAssignment) -> CollectTask:
        """追加分派记录并把任务推进到 ``ASSIGNED``。

        JSON 列必须整体重新赋值，原地 append 不会被 SQLAlchemy 检测为脏。
        """
        row = await self._require_row(task_id)
        row.assignments = [*row.assignments, assignment.model_dump(mode="json")]
        row.status = TaskStatus.ASSIGNED.value
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_task(row)

    async def increment_counters(
        self, task_id: str, *, collected: int = 0, published: int = 0
    ) -> CollectTask:
        """累加采集/发布计数。"""
        row = await self._require_row(task_id)
        row.collected_count += collected
        row.published_count += published
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_task(row)

    async def find_assigned_to(self, agent_id: str) -> list[CollectTask]:
        """查询分派给指定 Agent 的所有 assigned 状态任务（交互① Agent 重启拉取）。"""
        stmt = (
            select(CollectTaskRow)
            .where(CollectTaskRow.status == TaskStatus.ASSIGNED.value)
            .order_by(CollectTaskRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        # 过滤出真正包含该 agent_id 的任务
        return [
            row_to_task(row)
            for row in rows
            if any(a['agent_id'] == agent_id for a in row.assignments)
        ]

    async def _require_row(self, task_id: str) -> CollectTaskRow:
        """取行，不存在抛 KeyError。"""
        row = await self._session.get(CollectTaskRow, task_id)
        if row is None:
            raise KeyError(f"任务不存在：{task_id}")
        return row


__all__ = ["TaskRepository", "row_to_task"]
