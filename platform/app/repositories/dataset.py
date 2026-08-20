"""训练集仓储。

构建是异步的，所以受理时就要落一行 —— 否则 ``GET /datasets/{id}`` 无从查起，
「导出到哪一步了」只能翻日志（design.md 第 5 节要修的正是这个）。
"""

from datetime import UTC, datetime

from rdh_contract.enums import JobStatus
from rdh_contract.schemas import Dataset
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import DatasetRow


def row_to_dataset(row: DatasetRow) -> Dataset:
    """ORM 行 → contract 模型。"""
    return Dataset(
        dataset_id=row.dataset_id,
        status=JobStatus(row.status),
        episode_ids=tuple(row.episode_ids),
        output_format=row.output_format,
        requested_by=row.requested_by,
        manifest_key=row.manifest_key,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class DatasetRepository:
    """训练集数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        dataset_id: str,
        episode_ids: tuple[str, ...],
        output_format: str,
        requested_by: str,
    ) -> Dataset:
        """受理构建请求。

        落 ``PENDING`` —— 事件已发出但 worker 还没接手。状态推进要等
        tool-worker 落地（tasks.md 8.4-8.6），在那之前查到的都是 pending。
        """
        now = datetime.now(UTC)
        row = DatasetRow(
            dataset_id=dataset_id,
            status=JobStatus.PENDING.value,
            episode_ids=list(episode_ids),
            output_format=output_format,
            requested_by=requested_by,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_dataset(row)

    async def find_by_id(self, dataset_id: str) -> Dataset | None:
        """按 ID 查询，不存在返回 None。"""
        row = await self._session.get(DatasetRow, dataset_id)
        return row_to_dataset(row) if row else None


__all__ = ["DatasetRepository", "row_to_dataset"]
