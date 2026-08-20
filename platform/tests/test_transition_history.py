"""状态流转轨迹的记录规则。

四条纪律（design.md 第 7 节），每条都可能被后续改动破坏：

1. 正常推进留一条记录
2. 幂等重放不留 —— 否则 RabbitMQ 的重复投递会把轨迹淹没在假停顿里
3. 非法迁移不留 —— 守卫抛异常时状态没变，不该有痕迹
4. 触发者归属正确 —— 人工记 user_id，系统记环节名，不互相冒充

第 4 条最容易悄悄坏掉：把系统推进记成 admin 不会让任何测试变红，但会让轨迹说谎。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from rdh_contract.enums import EpisodeStatus, ReviewDecision
from rdh_contract.schemas import TransitionActor, VerifyResult
from rdh_contract.state_machine import INITIAL_STATE, InvalidTransitionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories.annotation import AnnotationRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.repositories.transition import TransitionRepository
from app.services.episode_lifecycle import EpisodeLifecycleService
from app.services.review import ReviewService

pytestmark = pytest.mark.integration

TASK_ID = "task-1"
AGENT_ID = "agent-local-01"
ANNOTATOR = "user-annotator"

SYSTEM = TransitionActor(actor_type="system", system_component="test_harness")


class _NullPublisher:
    """吞掉事件，本测试只关心轨迹。"""

    async def publish(self, routing_key: str, payload: object) -> str:
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
    """一条 Episode 加上推进它所需的服务与轨迹仓储。"""

    def __init__(self, session: AsyncSession) -> None:
        self.episodes = EpisodeRepository(session)
        self.transitions = TransitionRepository(session)
        self.lifecycle = EpisodeLifecycleService(
            episodes=self.episodes,
            publisher=_NullPublisher(),  # type: ignore[arg-type]
        )
        self.review = ReviewService(
            lifecycle=self.lifecycle,
            annotations=AnnotationRepository(session),
            episodes=self.episodes,
            tasks=TaskRepository(session),
        )
        self.episode_id = str(uuid.uuid4())

    async def create(self) -> None:
        await self.episodes.create(
            episode_id=self.episode_id,
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            status=INITIAL_STATE,
            recorded_by="user-1",
            robot_model="rm-75-6f",
            scene="kitchen",
        )

    async def push(self, target: EpisodeStatus, *, actor: TransitionActor = SYSTEM) -> None:
        await self.lifecycle.transition(self.episode_id, target=target, actor=actor)

    async def seed_to(self, status: EpisodeStatus) -> None:
        """沿主链路推到 ``status``。"""
        await self.create()
        for target in (
            EpisodeStatus.UPLOADING,
            EpisodeStatus.UPLOADED,
            EpisodeStatus.PROCESSING,
            EpisodeStatus.VERIFICATION_PENDING,
            EpisodeStatus.ANNOTATION_PROCESSING,
            EpisodeStatus.ANNOTATION_PENDING,
        ):
            await self.push(target)
            if target is status:
                return

    async def history(self) -> tuple[tuple[EpisodeStatus, EpisodeStatus], ...]:
        """轨迹压成 (from, to) 序列，便于断言。"""
        records = await self.transitions.get_history(self.episode_id)
        return tuple((r.from_status, r.to_status) for r in records)


class TestNormalProgressionRecords:
    """正常推进留下轨迹。"""

    async def test_single_transition_leaves_one_record(self, session: AsyncSession) -> None:
        h = _Harness(session)
        await h.create()
        await h.push(EpisodeStatus.UPLOADING)

        assert await h.history() == ((EpisodeStatus.RECORDING, EpisodeStatus.UPLOADING),)

    async def test_history_is_in_chronological_order(self, session: AsyncSession) -> None:
        """轨迹按时间正序 —— 停留时长靠相邻两条的时间差推导，顺序错了就算错。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PENDING)

        assert await h.history() == (
            (EpisodeStatus.RECORDING, EpisodeStatus.UPLOADING),
            (EpisodeStatus.UPLOADING, EpisodeStatus.UPLOADED),
            (EpisodeStatus.UPLOADED, EpisodeStatus.PROCESSING),
            (EpisodeStatus.PROCESSING, EpisodeStatus.VERIFICATION_PENDING),
            (EpisodeStatus.VERIFICATION_PENDING, EpisodeStatus.ANNOTATION_PROCESSING),
            (EpisodeStatus.ANNOTATION_PROCESSING, EpisodeStatus.ANNOTATION_PENDING),
        )

        records = await h.transitions.get_history(h.episode_id)
        times = [r.occurred_at for r in records]
        assert times == sorted(times)

    async def test_chain_is_contiguous(self, session: AsyncSession) -> None:
        """每条记录的 from 接上一条的 to —— 断链说明有状态变更绕过了记录点。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PENDING)

        pairs = await h.history()
        for previous, current in zip(pairs, pairs[1:], strict=False):
            assert previous[1] is current[0]


class TestReplayLeavesNoRecord:
    """幂等重放不留记录。"""

    async def test_replay_does_not_append(self, session: AsyncSession) -> None:
        """同一目标推两次，只留一条 —— 否则轨迹里出现假的停顿。"""
        h = _Harness(session)
        await h.create()
        await h.push(EpisodeStatus.UPLOADING)
        await h.push(EpisodeStatus.UPLOADING)

        assert await h.history() == ((EpisodeStatus.RECORDING, EpisodeStatus.UPLOADING),)

    async def test_no_self_loop_in_history(self, session: AsyncSession) -> None:
        """轨迹里不该出现 from == to。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await h.push(EpisodeStatus.VERIFICATION_PENDING)

        assert all(src is not dst for src, dst in await h.history())


class TestIllegalTransitionLeavesNoRecord:
    """非法迁移不留记录。"""

    async def test_rejected_transition_is_not_recorded(self, session: AsyncSession) -> None:
        """跳步被守卫拦住，状态没变，轨迹也不该多出一条。"""
        h = _Harness(session)
        await h.create()

        with pytest.raises(InvalidTransitionError):
            await h.push(EpisodeStatus.PUBLISHED)

        assert await h.history() == ()

    async def test_terminal_episode_stays_frozen(self, session: AsyncSession) -> None:
        """终态之后的任何尝试都不留痕。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await h.push(EpisodeStatus.REJECTED)
        before = await h.history()

        with pytest.raises(InvalidTransitionError):
            await h.push(EpisodeStatus.ANNOTATION_PROCESSING)

        assert await h.history() == before


class TestActorAttribution:
    """触发者归属。"""

    async def test_system_push_records_component_not_user(self, session: AsyncSession) -> None:
        """系统推进记环节名，user_id 必须为空 —— 不把系统伪装成某个用户。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.PROCESSING)
        await h.lifecycle.finish_processing(h.episode_id, succeeded=True)

        latest = (await h.transitions.get_history(h.episode_id))[-1]
        assert latest.to_status is EpisodeStatus.VERIFICATION_PENDING
        assert latest.actor.actor_type == "system"
        assert latest.actor.system_component == "scheduler"
        assert latest.actor.user_id is None

    async def test_manual_verification_records_user(self, session: AsyncSession) -> None:
        """人工质检记 user_id，system_component 必须为空。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await h.review.submit_verification(
            VerifyResult(
                episode_id=h.episode_id,
                decision=ReviewDecision.APPROVE,
                reason=None,
                checked_topics=("/camera/front/image_raw",),
                verified_by=ANNOTATOR,
                verified_at=datetime.now(UTC),
            )
        )

        latest = (await h.transitions.get_history(h.episode_id))[-1]
        assert latest.to_status is EpisodeStatus.ANNOTATION_PROCESSING
        assert latest.actor.actor_type == "user"
        assert latest.actor.user_id == ANNOTATOR
        assert latest.actor.system_component is None

    async def test_reject_reason_is_recorded(self, session: AsyncSession) -> None:
        """打回原因进轨迹 —— 「为什么死的」和「死在哪」一样重要。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await h.review.submit_verification(
            VerifyResult(
                episode_id=h.episode_id,
                decision=ReviewDecision.REJECT,
                reason="前视相机全程遮挡",
                checked_topics=("/camera/front/image_raw",),
                verified_by=ANNOTATOR,
                verified_at=datetime.now(UTC),
            )
        )

        latest = (await h.transitions.get_history(h.episode_id))[-1]
        assert latest.to_status is EpisodeStatus.REJECTED
        assert latest.reason == "前视相机全程遮挡"
        assert latest.actor.user_id == ANNOTATOR

    async def test_normal_progression_has_no_reason(self, session: AsyncSession) -> None:
        """正常推进不带原因，免得界面上每条都显示一句废话。"""
        h = _Harness(session)
        await h.create()
        await h.push(EpisodeStatus.UPLOADING)

        assert (await h.transitions.get_history(h.episode_id))[-1].reason is None


class TestDerailmentIsLocatable:
    """脱轨定位（修 stage.ts:78 的短板所依赖的数据）。"""

    async def test_failed_episode_records_where_it_died(self, session: AsyncSession) -> None:
        """失败前的最后一个正常状态可从轨迹读出 —— 进度条据此标出中断位置。"""
        h = _Harness(session)
        await h.seed_to(EpisodeStatus.ANNOTATION_PROCESSING)
        await h.lifecycle.finish_annotation_processing(
            h.episode_id, succeeded=False, error_message="送标算子容器启动失败"
        )

        records = await h.transitions.get_history(h.episode_id)
        assert records[-1].to_status is EpisodeStatus.FAILED
        # 死之前停在送标处理 —— 这正是 Episode 只存当前状态时拿不到的信息
        assert records[-1].from_status is EpisodeStatus.ANNOTATION_PROCESSING
        assert records[-1].reason == "送标算子容器启动失败"

    async def test_verification_reject_differs_from_review_reject(
        self, session: AsyncSession
    ) -> None:
        """质检打回与审核退回都源自人工，但源状态不同，轨迹能区分。"""
        rejected = _Harness(session)
        await rejected.seed_to(EpisodeStatus.VERIFICATION_PENDING)
        await rejected.push(EpisodeStatus.REJECTED)

        returned = _Harness(session)
        await returned.seed_to(EpisodeStatus.ANNOTATION_PENDING)
        await returned.push(EpisodeStatus.ANNOTATION_REVIEW)
        await returned.push(EpisodeStatus.ANNOTATION_PENDING)

        assert (await rejected.history())[-1] == (
            EpisodeStatus.VERIFICATION_PENDING,
            EpisodeStatus.REJECTED,
        )
        assert (await returned.history())[-1] == (
            EpisodeStatus.ANNOTATION_REVIEW,
            EpisodeStatus.ANNOTATION_PENDING,
        )


class TestHistoryIsolation:
    """轨迹按 Episode 隔离。"""

    async def test_history_only_returns_own_records(self, session: AsyncSession) -> None:
        one = _Harness(session)
        await one.seed_to(EpisodeStatus.PROCESSING)
        other = _Harness(session)
        await other.create()
        await other.push(EpisodeStatus.UPLOADING)

        assert len(await one.history()) == 3
        assert len(await other.history()) == 1

    async def test_unknown_episode_has_empty_history(self, session: AsyncSession) -> None:
        """不存在的 Episode 返回空 —— 路由层负责把它转成 404。"""
        repo = TransitionRepository(session)
        assert await repo.get_history(str(uuid.uuid4())) == ()
