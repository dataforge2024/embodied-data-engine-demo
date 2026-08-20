"""核验、标注、审核编排（交互④的服务端逻辑）。

三个环节都要先确认 Episode 处于正确状态，再落数据，最后经 lifecycle 推进状态。
顺序反了会出现「标注存了但状态没动」这种半完成态。
"""

import uuid

from rdh_contract.enums import EpisodeStatus, ReviewDecision
from rdh_contract.schemas import (
    Annotation,
    AnnotationSubmit,
    Episode,
    ReviewResult,
    VerifyResult,
)

from app.repositories.annotation import AnnotationRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.services.episode_lifecycle import EpisodeLifecycleService, TransitionOutcome


class ReviewService:
    """人工环节编排。"""

    def __init__(
        self,
        *,
        lifecycle: EpisodeLifecycleService,
        annotations: AnnotationRepository,
        episodes: EpisodeRepository,
        tasks: TaskRepository,
    ) -> None:
        self._lifecycle = lifecycle
        self._annotations = annotations
        self._episodes = episodes
        self._tasks = tasks

    async def submit_verification(self, result: VerifyResult) -> TransitionOutcome:
        """提交核验结果。

        通过 → ``annotation_processing``（送标处理，异步）；打回 → ``rejected`` 终态并发事件。

        通过后不再直接进 ``annotation_pending`` —— 中间多了一个送标环节，由 Scheduler
        处理完再回调推进。理由见
        ``openspec/changes/manual-workflow-progression/design.md`` 第 1 节。
        """
        await self._lifecycle.assert_actionable(
            result.episode_id, expected=EpisodeStatus.VERIFICATION_PENDING
        )
        await self._annotations.upsert_verify_result(annotation_id=str(uuid.uuid4()), result=result)

        if result.decision is ReviewDecision.REJECT:
            return await self._lifecycle.reject(
                result.episode_id,
                reason=result.reason or "核验未通过",
                rejected_by=result.verified_by,
            )
        return await self._lifecycle.transition(
            result.episode_id, target=EpisodeStatus.ANNOTATION_PROCESSING
        )

    async def submit_annotation(
        self, submission: AnnotationSubmit, *, annotated_by: str
    ) -> tuple[Annotation, TransitionOutcome]:
        """提交标注。分段全量替换，随后进入审核。"""
        await self._lifecycle.assert_actionable(
            submission.episode_id, expected=EpisodeStatus.ANNOTATION_PENDING
        )

        annotation = await self._annotations.save_segments(
            annotation_id=str(uuid.uuid4()),
            episode_id=submission.episode_id,
            segments=submission.segments,
            notes=submission.notes,
            annotated_by=annotated_by,
        )
        # Episode 与 Annotation 两处都存分段：前者供查询与训练集构建，后者留审核轨迹
        await self._episodes.replace_segments(submission.episode_id, submission.segments)
        outcome = await self._lifecycle.transition(
            submission.episode_id, target=EpisodeStatus.ANNOTATION_REVIEW
        )
        return annotation, outcome

    async def submit_review(self, result: ReviewResult) -> TransitionOutcome:
        """提交审核结果。

        通过 → ``published`` 并发 ``annotation.approved``；
        退回 → 回到 ``annotation_pending`` 重做（``revision`` +1），**不是** ``rejected``。
        """
        await self._lifecycle.assert_actionable(
            result.episode_id, expected=EpisodeStatus.ANNOTATION_REVIEW
        )
        annotation = await self._annotations.save_review_result(result)

        if result.decision is ReviewDecision.REJECT:
            return await self._lifecycle.transition(
                result.episode_id, target=EpisodeStatus.ANNOTATION_PENDING
            )

        outcome = await self._lifecycle.publish_episode(
            result.episode_id,
            annotation_id=annotation.annotation_id,
            segment_count=len(annotation.segments),
            approved_by=result.reviewed_by,
        )
        if outcome.changed:
            await self._tasks.increment_counters(outcome.episode.task_id, published=1)
        return outcome

    async def verification_queue(
        self, *, page: int = 1, limit: int = 20
    ) -> tuple[tuple[Episode, ...], int]:
        """待核验队列（FIFO）。"""
        return await self._episodes.find_all(
            status=EpisodeStatus.VERIFICATION_PENDING, page=page, limit=limit
        )

    async def annotation_queue(
        self, *, page: int = 1, limit: int = 20
    ) -> tuple[tuple[Episode, ...], int]:
        """待标注队列（FIFO）。"""
        return await self._episodes.find_all(
            status=EpisodeStatus.ANNOTATION_PENDING, page=page, limit=limit
        )

    async def review_queue(
        self, *, page: int = 1, limit: int = 20
    ) -> tuple[tuple[Episode, ...], int]:
        """待审核队列（FIFO）。"""
        return await self._episodes.find_all(
            status=EpisodeStatus.ANNOTATION_REVIEW, page=page, limit=limit
        )


__all__ = ["ReviewService"]
