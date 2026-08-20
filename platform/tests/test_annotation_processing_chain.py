"""质检通过 → 送标处理 → 待标注 的状态链。

回归点：质检通过后 **不再直连** ``annotation_pending``，中间多了一个由 Scheduler
回调推进的送标环节（design.md 第 1 节）。这条链断在任何一环，人工工作流就卡住 ——
而卡住的表现是「点了质检没反应」，不易定位。

跳步同样要测：契约守卫在，但守卫要真的被 service 层调到才有用。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from rdh_contract.enums import EpisodeStatus, ReviewDecision
from rdh_contract.schemas import AnnotationSubmit, Segment, TransitionActor, VerifyResult
from rdh_contract.schemas.scheduler import AnnotationProcessingCallback
from rdh_contract.state_machine import INITIAL_STATE, InvalidTransitionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories.algo_job_run import AlgoJobRunRepository
from app.repositories.annotation import AnnotationRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.services.callbacks import CallbackService
from app.services.episode_lifecycle import EpisodeLifecycleService
from app.services.review import ReviewService

pytestmark = pytest.mark.integration

TASK_ID = "task-1"
AGENT_ID = "agent-local-01"
ANNOTATOR = "user-annotator"


class _NullPublisher:
    """吞掉事件，本测试只关心状态链。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    async def publish(self, routing_key: str, payload: object) -> str:
        self.published.append((routing_key, payload))
        return str(uuid.uuid4())


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app import models  # noqa: F401 — 注册模型到 metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


class _Harness:
    """一条走到指定状态的 Episode，外加它需要的服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self.episodes = EpisodeRepository(session)
        self.publisher = _NullPublisher()
        self.lifecycle = EpisodeLifecycleService(
            episodes=self.episodes,
            publisher=self.publisher,  # type: ignore[arg-type]
        )
        self.review = ReviewService(
            lifecycle=self.lifecycle,
            annotations=AnnotationRepository(session),
            episodes=self.episodes,
            tasks=TaskRepository(session),
        )
        self.callbacks = CallbackService(
            lifecycle=self.lifecycle,
            episodes=self.episodes,
            tasks=TaskRepository(session),
            object_store=None,  # type: ignore[arg-type]  # 本测试不碰对象存储
            algo_job_runs=AlgoJobRunRepository(session),
        )
        self.episode_id = str(uuid.uuid4())

    async def seed_to(self, status: EpisodeStatus) -> None:
        """把 Episode 沿主链路推到 ``status``。"""
        await self.episodes.create(
            episode_id=self.episode_id,
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            status=INITIAL_STATE,
            recorded_by="user-1",
            robot_model="rm-75-6f",
            scene="kitchen",
        )
        path = (
            EpisodeStatus.UPLOADING,
            EpisodeStatus.UPLOADED,
            EpisodeStatus.PROCESSING,
            EpisodeStatus.VERIFICATION_PENDING,
            EpisodeStatus.ANNOTATION_PROCESSING,
            EpisodeStatus.ANNOTATION_PENDING,
        )
        actor = TransitionActor(actor_type="system", system_component="test_harness")
        for target in path:
            if target is status:
                await self.lifecycle.transition(self.episode_id, target=target, actor=actor)
                return
            await self.lifecycle.transition(self.episode_id, target=target, actor=actor)

    def verify(self, decision: ReviewDecision, reason: str | None = None) -> VerifyResult:
        return VerifyResult(
            episode_id=self.episode_id,
            decision=decision,
            reason=reason,
            checked_topics=("/camera/front/image_raw",),
            verified_by=ANNOTATOR,
            verified_at=datetime.now(UTC),
        )

    def processing_done(self, *, succeeded: bool = True) -> AnnotationProcessingCallback:
        return AnnotationProcessingCallback(
            episode_id=self.episode_id,
            succeeded=succeeded,
            error_message=None if succeeded else "送标算子容器启动失败",
            reported_at=datetime.now(UTC),
        )


class TestVerificationEntersProcessing:
    """质检通过 → 送标处理。"""

    async def test_approve_goes_to_annotation_processing(self, session: AsyncSession) -> None:
        """通过后落 annotation_processing，而不是 annotation_pending。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)

        outcome = await h.review.submit_verification(h.verify(ReviewDecision.APPROVE))

        assert outcome.changed
        assert outcome.episode.status is EpisodeStatus.ANNOTATION_PROCESSING

    async def test_reject_still_terminal(self, session: AsyncSession) -> None:
        """打回仍是 rejected 终态，没被送标环节带偏。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)

        outcome = await h.review.submit_verification(
            h.verify(ReviewDecision.REJECT, reason="前视相机全程遮挡")
        )

        assert outcome.episode.status is EpisodeStatus.REJECTED
        assert [key for key, _ in h.publisher.published] == ["episode.rejected"]


class TestProcessingCallbackAdvances:
    """送标回调 → 待标注 / 失败。"""

    async def test_success_enters_annotation_pending(self, session: AsyncSession) -> None:
        """送标完成后人工才能开始标注。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PROCESSING)

        outcome = await h.callbacks.handle_annotation_processing(h.processing_done())

        assert outcome.changed
        assert outcome.episode.status is EpisodeStatus.ANNOTATION_PENDING

    async def test_failure_lands_failed_with_reason(self, session: AsyncSession) -> None:
        """送标失败落 failed 并记原因 —— 否则查不出为什么标不了。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PROCESSING)

        outcome = await h.callbacks.handle_annotation_processing(
            h.processing_done(succeeded=False)
        )

        assert outcome.episode.status is EpisodeStatus.FAILED
        assert outcome.episode.reject_reason == "送标算子容器启动失败"

    async def test_replayed_callback_is_idempotent(self, session: AsyncSession) -> None:
        """RabbitMQ 至少一次投递：重复回调不该报错。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PROCESSING)

        await h.callbacks.handle_annotation_processing(h.processing_done())
        again = await h.callbacks.handle_annotation_processing(h.processing_done())

        assert not again.changed
        assert again.episode.status is EpisodeStatus.ANNOTATION_PENDING


class TestFullChain:
    """质检 → 送标 → 标注 走通一遍。"""

    async def test_chain_reaches_annotation_review(self, session: AsyncSession) -> None:
        """三步下来 Episode 进审核队列，中间没有断点。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)

        await h.review.submit_verification(h.verify(ReviewDecision.APPROVE))
        await h.callbacks.handle_annotation_processing(h.processing_done())
        _, outcome = await h.review.submit_annotation(
            AnnotationSubmit(
                episode_id=h.episode_id,
                segments=(
                    Segment(
                        segment_id=str(uuid.uuid4()),
                        start_ms=0,
                        end_ms=1200,
                        action_label="grasp",
                    ),
                ),
                notes="抓取动作完整",
            ),
            annotated_by=ANNOTATOR,
        )

        assert outcome.episode.status is EpisodeStatus.ANNOTATION_REVIEW


class TestSkippingSteps:
    """点错顺序要 409（守卫抛 InvalidTransitionError，上层转 409），不能静默改状态。"""

    async def test_cannot_annotate_while_processing(self, session: AsyncSession) -> None:
        """送标还没完成就提交标注 —— 拦住。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PROCESSING)

        with pytest.raises(InvalidTransitionError):
            await h.review.submit_annotation(
                AnnotationSubmit(
                    episode_id=h.episode_id,
                    segments=(
                        Segment(segment_id=str(uuid.uuid4()), start_ms=0, end_ms=900),
                    ),
                    notes=None,
                ),
                annotated_by=ANNOTATOR,
            )

    async def test_cannot_verify_twice(self, session: AsyncSession) -> None:
        """质检提交两次 —— 第二次已不在 verification_pending，拦住。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await h.review.submit_verification(h.verify(ReviewDecision.APPROVE))

        with pytest.raises(InvalidTransitionError):
            await h.review.submit_verification(h.verify(ReviewDecision.APPROVE))

    async def test_processing_callback_rejected_before_verification(
        self, session: AsyncSession
    ) -> None:
        """质检还没做就来送标回调 —— 源状态不对，拦住。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)

        with pytest.raises(InvalidTransitionError):
            await h.callbacks.handle_annotation_processing(h.processing_done())
