"""算子运行日志。

三件事要守住：

1. 每个算子结果都落一条记录，不管 ``pipeline_complete`` ——单个算子完成也要能看到它跑了
2. 失败也要记，且带 error_message —— 排障靠这个字段
3. 按 episode 隔离查询，且按时间正序返回
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from rdh_contract.enums import AlgoOperator, EpisodeStatus, JobStatus
from rdh_contract.schemas import Segment, TransitionActor
from rdh_contract.schemas.scheduler import AlgoJobResult, AlgoResultCallback
from rdh_contract.state_machine import INITIAL_STATE
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories.algo_job_run import AlgoJobRunRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.services.callbacks import CallbackService
from app.services.episode_lifecycle import EpisodeLifecycleService

pytestmark = pytest.mark.integration

TASK_ID = "task-1"
AGENT_ID = "agent-local-01"


class _NullPublisher:
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


_SYSTEM = TransitionActor(actor_type="system", system_component="test_harness")


class _Harness:
    """一条走到 processing 的 Episode，外加它需要的服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self.episodes = EpisodeRepository(session)
        self.algo_job_runs = AlgoJobRunRepository(session)
        self.lifecycle = EpisodeLifecycleService(
            episodes=self.episodes,
            publisher=_NullPublisher(),  # type: ignore[arg-type]
        )
        self.callbacks = CallbackService(
            lifecycle=self.lifecycle,
            episodes=self.episodes,
            tasks=TaskRepository(session),
            object_store=None,  # type: ignore[arg-type]  # 本测试不碰对象存储
            algo_job_runs=self.algo_job_runs,
        )
        self.episode_id = str(uuid.uuid4())

    async def seed_to_processing(self) -> None:
        await self.episodes.create(
            episode_id=self.episode_id,
            task_id=TASK_ID,
            agent_id=AGENT_ID,
            status=INITIAL_STATE,
            recorded_by="user-1",
        )
        for target in (
            EpisodeStatus.UPLOADING,
            EpisodeStatus.UPLOADED,
            EpisodeStatus.PROCESSING,
        ):
            await self.lifecycle.transition(
                self.episode_id,
                target=target,
                actor=_SYSTEM,
            )


def _result(
    *,
    operator: AlgoOperator,
    status: JobStatus = JobStatus.SUCCEEDED,
    error_message: str | None = None,
    segments: tuple[Segment, ...] = (),
) -> AlgoJobResult:
    started = datetime.now(UTC)
    return AlgoJobResult(
        job_id=str(uuid.uuid4()),
        episode_id="unused",  # 覆盖不了 —— callback 层按 callback.episode_id 记
        operator=operator,
        status=status,
        model_version="v1.0.0",
        segments=segments,
        error_message=error_message,
        started_at=started,
        finished_at=started + timedelta(seconds=3),
    )


class TestEachResultIsLogged:
    """每个算子结果都落一条记录。"""

    async def test_single_operator_completion_logs_one_run(
        self, session: AsyncSession
    ) -> None:
        """单个算子完成（pipeline_complete=False）也要落日志 —— 不是只有流水线整体完成才记。"""
        h = _Harness(session)
        await h.seed_to_processing()

        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(_result(operator=AlgoOperator.QUALITY),),
                pipeline_complete=False,
                reported_at=datetime.now(UTC),
            )
        )

        history = await h.algo_job_runs.get_history(h.episode_id)
        assert len(history) == 1
        assert history[0].operator is AlgoOperator.QUALITY
        assert history[0].status is JobStatus.SUCCEEDED

    async def test_multiple_results_in_one_callback_all_logged(
        self, session: AsyncSession
    ) -> None:
        """一次回调带多个算子结果，每个都要落一条，不是只留最后一个。"""
        h = _Harness(session)
        await h.seed_to_processing()

        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(
                    _result(operator=AlgoOperator.QUALITY),
                    _result(operator=AlgoOperator.KEYFRAME),
                    _result(
                        operator=AlgoOperator.PREANNOTATE,
                        segments=(
                            Segment(
                                segment_id=str(uuid.uuid4()),
                                start_ms=0,
                                end_ms=1000,
                                action_label="grasp",
                            ),
                        ),
                    ),
                ),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )

        history = await h.algo_job_runs.get_history(h.episode_id)
        assert {r.operator for r in history} == {
            AlgoOperator.QUALITY,
            AlgoOperator.KEYFRAME,
            AlgoOperator.PREANNOTATE,
        }

    async def test_two_callbacks_accumulate_not_overwrite(
        self, session: AsyncSession
    ) -> None:
        """算子分批回调（先 QUALITY 再 KEYFRAME），日志要累加，不能后一条覆盖前一条。"""
        h = _Harness(session)
        await h.seed_to_processing()

        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(_result(operator=AlgoOperator.QUALITY),),
                pipeline_complete=False,
                reported_at=datetime.now(UTC),
            )
        )
        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(_result(operator=AlgoOperator.KEYFRAME),),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )

        history = await h.algo_job_runs.get_history(h.episode_id)
        assert len(history) == 2


class TestFailureIsLogged:
    """失败要记，且带原因。"""

    async def test_failed_operator_logs_error_message(self, session: AsyncSession) -> None:
        h = _Harness(session)
        await h.seed_to_processing()

        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(
                    _result(
                        operator=AlgoOperator.ANOMALY,
                        status=JobStatus.FAILED,
                        error_message="模型加载失败：显存不足",
                    ),
                ),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )

        history = await h.algo_job_runs.get_history(h.episode_id)
        assert history[0].status is JobStatus.FAILED
        assert history[0].error_message == "模型加载失败：显存不足"

    async def test_partial_failure_still_logs_succeeded_ones(
        self, session: AsyncSession
    ) -> None:
        """一个算子挂了，其余成功的也不该被拖累丢失日志。"""
        h = _Harness(session)
        await h.seed_to_processing()

        await h.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=h.episode_id,
                results=(
                    _result(operator=AlgoOperator.QUALITY, status=JobStatus.SUCCEEDED),
                    _result(
                        operator=AlgoOperator.ANOMALY,
                        status=JobStatus.FAILED,
                        error_message="超时",
                    ),
                ),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )

        history = await h.algo_job_runs.get_history(h.episode_id)
        statuses = {r.operator: r.status for r in history}
        assert statuses[AlgoOperator.QUALITY] is JobStatus.SUCCEEDED
        assert statuses[AlgoOperator.ANOMALY] is JobStatus.FAILED


class TestHistoryIsolationAndOrder:
    """按 Episode 隔离，按时间正序。"""

    async def test_history_only_returns_own_records(self, session: AsyncSession) -> None:
        one = _Harness(session)
        await one.seed_to_processing()
        other = _Harness(session)
        await other.seed_to_processing()

        await one.callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=one.episode_id,
                results=(_result(operator=AlgoOperator.QUALITY),),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )

        assert len(await one.algo_job_runs.get_history(one.episode_id)) == 1
        assert len(await other.algo_job_runs.get_history(other.episode_id)) == 0

    async def test_unknown_episode_has_empty_history(self, session: AsyncSession) -> None:
        repo = AlgoJobRunRepository(session)
        assert await repo.get_history(str(uuid.uuid4())) == ()
