"""上传完成后必须进入 ``processing``。

回归测试：Scheduler 只上报结果、不改 Platform 状态，所以 ``uploaded → processing``
这一跳得由 Platform 在发出 ``episode.uploaded`` 时自己做。早先没人做，导致
Scheduler 回调 algo-result 时 ``processing → verification_pending`` 因当前仍是
``uploaded`` 而非法（409），而 409 被 Scheduler 当成「重放，视为成功」咽掉 ——
日志一切正常，实际状态一步没动、算子产物也没落库。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from rdh_contract.enums import EpisodeStatus
from rdh_contract.state_machine import INITIAL_STATE
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories.episode import EpisodeRepository
from app.services.episode_lifecycle import EpisodeLifecycleService

pytestmark = pytest.mark.integration

TASK_ID = "task-1"
AGENT_ID = "agent-local-01"


class _RecordingPublisher:
    """记录发出的事件，不真的写队列。"""

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


async def _uploaded_episode(
    session: AsyncSession,
) -> tuple[str, EpisodeRepository, _RecordingPublisher]:
    """建一条走到 uploading 的 Episode，再跑 mark_uploaded。"""
    episodes = EpisodeRepository(session)
    publisher = _RecordingPublisher()
    lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)  # type: ignore[arg-type]

    episode_id = str(uuid.uuid4())
    await episodes.create(
        episode_id=episode_id,
        task_id=TASK_ID,
        agent_id=AGENT_ID,
        status=INITIAL_STATE,
        recorded_by="user-1",
        robot_model="rm-75-6f",
        scene="kitchen",
    )
    await lifecycle.transition(episode_id, target=EpisodeStatus.UPLOADING)
    await lifecycle.mark_uploaded(
        episode_id,
        object_key=f"episodes/{episode_id}/raw.mcap",
        size_bytes=303052,
        checksum="a" * 64,
        duration_ms=16480,
        recorded_topics=("/camera/front/image_raw", "/joint_states"),
    )
    return episode_id, episodes, publisher


class TestUploadedEntersProcessing:
    async def test_status_is_processing(self, session: AsyncSession) -> None:
        """落定的状态是 processing，不是 uploaded。"""
        episode_id, episodes, _ = await _uploaded_episode(session)

        stored = await episodes.find_by_id(episode_id)
        assert stored is not None
        assert stored.status is EpisodeStatus.PROCESSING

    async def test_upload_result_still_attached(self, session: AsyncSession) -> None:
        """推进状态不能把上传产物冲掉。"""
        episode_id, episodes, _ = await _uploaded_episode(session)

        stored = await episodes.find_by_id(episode_id)
        assert stored is not None
        assert stored.object_key == f"episodes/{episode_id}/raw.mcap"
        assert stored.size_bytes == 303052
        assert stored.duration_ms == 16480

    async def test_event_still_published(self, session: AsyncSession) -> None:
        """episode.uploaded 仍要发 —— Scheduler 靠它启动流水线。"""
        _, _, publisher = await _uploaded_episode(session)

        assert [key for key, _ in publisher.published] == ["episode.uploaded"]

    async def test_scheduler_callback_transition_is_legal(
        self, session: AsyncSession
    ) -> None:
        """这就是当初 409 的那一跳：现在必须合法。"""
        episode_id, episodes, publisher = await _uploaded_episode(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)  # type: ignore[arg-type]

        outcome = await lifecycle.finish_processing(episode_id, succeeded=True)

        assert outcome.changed
        assert outcome.episode.status is EpisodeStatus.VERIFICATION_PENDING

    async def test_replayed_callback_is_idempotent(
        self, session: AsyncSession
    ) -> None:
        """Agent 重发上传回调不该报错 —— 已推进过就直接返回。"""
        episode_id, episodes, publisher = await _uploaded_episode(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)  # type: ignore[arg-type]

        outcome = await lifecycle.mark_uploaded(
            episode_id,
            object_key=f"episodes/{episode_id}/raw.mcap",
            size_bytes=303052,
            checksum="a" * 64,
            duration_ms=16480,
            recorded_topics=("/camera/front/image_raw", "/joint_states"),
        )

        assert not outcome.changed
        assert outcome.episode.status is EpisodeStatus.PROCESSING
        # 事件只发一次，不该因重放再投一条
        assert len(publisher.published) == 1
