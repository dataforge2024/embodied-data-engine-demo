"""标注记录仓储。"""

from datetime import UTC, datetime

from rdh_contract.schemas import Annotation, ReviewResult, Segment, VerifyResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.annotation import AnnotationRow


def row_to_annotation(row: AnnotationRow) -> Annotation:
    """ORM 行 → contract 模型。"""
    return Annotation(
        annotation_id=row.annotation_id,
        episode_id=row.episode_id,
        segments=tuple(Segment(**s) for s in row.segments),
        notes=row.notes,
        verify_result=VerifyResult(**row.verify_result) if row.verify_result else None,
        review_result=ReviewResult(**row.review_result) if row.review_result else None,
        revision=row.revision,
        annotated_by=row.annotated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AnnotationRepository:
    """标注记录数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_episode(self, episode_id: str) -> Annotation | None:
        """按 Episode 查询标注记录。"""
        stmt = select(AnnotationRow).where(AnnotationRow.episode_id == episode_id)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return row_to_annotation(row) if row else None

    async def upsert_verify_result(self, *, annotation_id: str, result: VerifyResult) -> Annotation:
        """写入核验结果。记录不存在则创建（核验发生在标注之前）。"""
        row = await self._find_row(result.episode_id)
        now = datetime.now(UTC)
        if row is None:
            row = AnnotationRow(
                annotation_id=annotation_id,
                episode_id=result.episode_id,
                segments=[],
                verify_result=result.model_dump(mode="json"),
                revision=1,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.verify_result = result.model_dump(mode="json")
            row.updated_at = now
        await self._session.flush()
        return row_to_annotation(row)

    async def save_segments(
        self,
        *,
        annotation_id: str,
        episode_id: str,
        segments: tuple[Segment, ...],
        notes: str | None,
        annotated_by: str,
    ) -> Annotation:
        """写入标注分段（全量替换）。"""
        row = await self._find_row(episode_id)
        now = datetime.now(UTC)
        payload = [s.model_dump(mode="json") for s in segments]
        if row is None:
            row = AnnotationRow(
                annotation_id=annotation_id,
                episode_id=episode_id,
                segments=payload,
                notes=notes,
                annotated_by=annotated_by,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.segments = payload
            row.notes = notes
            row.annotated_by = annotated_by
            row.updated_at = now
        await self._session.flush()
        return row_to_annotation(row)

    async def save_review_result(self, result: ReviewResult) -> Annotation:
        """写入审核结果。退回重做时 ``revision`` +1。"""
        row = await self._require_row(result.episode_id)
        row.review_result = result.model_dump(mode="json")
        if result.decision.value == "reject":
            row.revision += 1
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        return row_to_annotation(row)

    async def _find_row(self, episode_id: str) -> AnnotationRow | None:
        stmt = select(AnnotationRow).where(AnnotationRow.episode_id == episode_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _require_row(self, episode_id: str) -> AnnotationRow:
        row = await self._find_row(episode_id)
        if row is None:
            raise KeyError(f"标注记录不存在：{episode_id}")
        return row


__all__ = ["AnnotationRepository", "row_to_annotation"]
