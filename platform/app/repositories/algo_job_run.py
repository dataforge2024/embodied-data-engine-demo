"""算子运行日志仓储。

只追加，不更新也不删除 —— 日志是既成事实，与 :mod:`app.repositories.transition`
同一套约定（POC 阶段没有归档策略）。
"""

from rdh_contract.schemas import AlgoJobResult, AlgoJobRunRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.algo_job_run import AlgoJobRunRow


def row_to_record(row: AlgoJobRunRow) -> AlgoJobRunRecord:
    """ORM 行 → contract 模型。"""
    return AlgoJobRunRecord(
        episode_id=row.episode_id,
        job_id=row.job_id,
        operator=row.operator,
        status=row.status,
        model_version=row.model_version,
        error_message=row.error_message,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class AlgoJobRunRepository:
    """算子运行日志数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, episode_id: str, result: AlgoJobResult) -> AlgoJobRunRecord:
        """追加一条算子运行记录。"""
        row = AlgoJobRunRow(
            episode_id=episode_id,
            job_id=result.job_id,
            operator=result.operator.value,
            status=result.status.value,
            model_version=result.model_version,
            error_message=result.error_message,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_record(row)

    async def get_history(self, episode_id: str) -> tuple[AlgoJobRunRecord, ...]:
        """按时间正序返回该 Episode 的算子运行记录。"""
        stmt = (
            select(AlgoJobRunRow)
            .where(AlgoJobRunRow.episode_id == episode_id)
            .order_by(AlgoJobRunRow.started_at, AlgoJobRunRow.id)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(row_to_record(r) for r in rows)


__all__ = ["AlgoJobRunRepository", "row_to_record"]
