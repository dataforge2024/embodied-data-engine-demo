"""训练集构建。

三件事要守住：

1. 只有 published 的 Episode 能纳入 —— 未定稿的标注进了清单，下游拿到的是半成品
2. 清单里的分段是人工最终版，不是算子预标注
3. 失败要落 failed 并记原因 —— 不落的话调用方查到的一直是 running，等一个不会来的结果
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from rdh_contract.enums import AlgoOperator, EpisodeStatus, JobStatus
from rdh_contract.schemas import Segment, TransitionActor
from rdh_contract.state_machine import INITIAL_STATE
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.repositories.dataset import DatasetRepository
from app.repositories.episode import EpisodeRepository
from app.services.dataset_builder import (
    MANIFEST_VERSION,
    DatasetBuilder,
    DatasetBuildError,
)
from app.services.object_store import LocalObjectStore

pytestmark = pytest.mark.integration

SYSTEM = TransitionActor(actor_type="system", system_component="test_harness")


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
    def __init__(self, session: AsyncSession, root: Path) -> None:
        self.episodes = EpisodeRepository(session)
        self.datasets = DatasetRepository(session)
        self.store = LocalObjectStore(root)
        self.builder = DatasetBuilder(
            datasets=self.datasets, episodes=self.episodes, object_store=self.store
        )

    async def published_episode(self, *, segments: tuple[Segment, ...] = ()) -> str:
        """建一条走到 published 的 Episode。"""
        episode_id = str(uuid.uuid4())
        await self.episodes.create(
            episode_id=episode_id,
            task_id="task-1",
            agent_id="agent-1",
            status=INITIAL_STATE,
            recorded_by="user-1",
            robot_model="rm-75-6f",
            scene="kitchen",
        )
        await self.episodes.attach_upload_result(
            episode_id,
            object_key=f"episodes/{episode_id}/raw.mcap",
            size_bytes=1024,
            checksum="a" * 64,
            duration_ms=8000,
        )
        for target in (
            EpisodeStatus.UPLOADING,
            EpisodeStatus.UPLOADED,
            EpisodeStatus.PROCESSING,
            EpisodeStatus.VERIFICATION_PENDING,
            EpisodeStatus.ANNOTATION_PROCESSING,
            EpisodeStatus.ANNOTATION_PENDING,
            EpisodeStatus.ANNOTATION_REVIEW,
            EpisodeStatus.PUBLISHED,
        ):
            await self.episodes.apply_transition(episode_id, target=target, actor=SYSTEM)
        if segments:
            await self.episodes.replace_segments(episode_id, segments)
        return episode_id

    async def episode_at(self, status: EpisodeStatus) -> str:
        """建一条停在 ``status`` 的 Episode。"""
        episode_id = str(uuid.uuid4())
        await self.episodes.create(
            episode_id=episode_id,
            task_id="task-1",
            agent_id="agent-1",
            status=INITIAL_STATE,
            recorded_by="user-1",
        )
        for target in (
            EpisodeStatus.UPLOADING,
            EpisodeStatus.UPLOADED,
            EpisodeStatus.PROCESSING,
            EpisodeStatus.VERIFICATION_PENDING,
        ):
            await self.episodes.apply_transition(episode_id, target=target, actor=SYSTEM)
            if target is status:
                break
        return episode_id

    async def accept(self, episode_ids: tuple[str, ...]) -> str:
        dataset = await self.datasets.create(
            dataset_id=str(uuid.uuid4()),
            episode_ids=episode_ids,
            output_format="lerobot",
            requested_by="user-admin",
        )
        return dataset.dataset_id

    def manifest(self, manifest_key: str) -> dict:
        return json.loads(self.store.path_for(manifest_key).read_text(encoding="utf-8"))


def _segment(label: str, start: int, end: int, *, manual: bool) -> Segment:
    return Segment(
        segment_id=str(uuid.uuid4()),
        start_ms=start,
        end_ms=end,
        action_label=label,
        description="人工确认" if manual else None,
        # source=None 表示人工改过，不再是算子产出
        source=None if manual else AlgoOperator.PREANNOTATE,
        confidence=None,
    )


class TestSuccessfulBuild:
    """构建成功路径。"""

    async def test_status_reaches_succeeded_with_manifest_key(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        h = _Harness(session, tmp_path)
        episode_id = await h.published_episode()
        dataset_id = await h.accept((episode_id,))

        built = await h.builder.build(dataset_id)

        assert built.status is JobStatus.SUCCEEDED
        assert built.manifest_key == f"datasets/{dataset_id}/manifest.json"
        assert built.failure_reason is None

    async def test_manifest_file_is_written(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """产物真的落在对象存储上 —— 演示时点导出要有东西可看。"""
        h = _Harness(session, tmp_path)
        episode_id = await h.published_episode()
        dataset_id = await h.accept((episode_id,))

        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        assert h.store.exists(built.manifest_key)

        manifest = h.manifest(built.manifest_key)
        assert manifest["manifest_version"] == MANIFEST_VERSION
        assert manifest["dataset_id"] == dataset_id
        assert manifest["output_format"] == "lerobot"
        assert manifest["requested_by"] == "user-admin"
        assert manifest["episode_count"] == 1

    async def test_manifest_carries_manual_segments(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """清单里的分段是人工最终版 —— 不是算子预标注那份。"""
        h = _Harness(session, tmp_path)
        segments = (
            _segment("grasp", 0, 1200, manual=True),
            _segment("move", 1200, 2400, manual=True),
        )
        episode_id = await h.published_episode(segments=segments)
        dataset_id = await h.accept((episode_id,))

        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        manifest = h.manifest(built.manifest_key)

        entry = manifest["episodes"][0]
        assert entry["episode_id"] == episode_id
        assert [s["action_label"] for s in entry["segments"]] == ["grasp", "move"]
        # 人工改过的分段 source 为 null
        assert all(s["source"] is None for s in entry["segments"])
        assert manifest["segment_count"] == 2

    async def test_manifest_records_provenance(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """清单带上产物位置与校验和，下游据此取原始数据。"""
        h = _Harness(session, tmp_path)
        episode_id = await h.published_episode()
        dataset_id = await h.accept((episode_id,))

        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        entry = h.manifest(built.manifest_key)["episodes"][0]

        assert entry["object_key"] == f"episodes/{episode_id}/raw.mcap"
        assert entry["checksum"] == "a" * 64
        assert entry["size_bytes"] == 1024
        assert entry["duration_ms"] == 8000
        assert entry["robot_model"] == "rm-75-6f"
        assert entry["status"] == "published"

    async def test_manifest_says_it_is_not_trainable(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """清单自己说明不是可训练数据，免得下游误用（design.md 第 5 节）。"""
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((await h.published_episode(),))

        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        assert "并非可直接训练" in h.manifest(built.manifest_key)["format_note"]

    async def test_multiple_episodes_all_listed(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        h = _Harness(session, tmp_path)
        ids = tuple(
            [
                await h.published_episode(segments=(_segment("a", 0, 100, manual=True),)),
                await h.published_episode(segments=(_segment("b", 0, 200, manual=True),)),
            ]
        )
        dataset_id = await h.accept(ids)

        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        manifest = h.manifest(built.manifest_key)

        assert manifest["episode_count"] == 2
        assert manifest["segment_count"] == 2
        assert [e["episode_id"] for e in manifest["episodes"]] == list(ids)

    async def test_algo_artifacts_listed_only_when_present(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """只列真的存在的算子产物 —— 挂一个取不到的键，下游会当成损坏。"""
        h = _Harness(session, tmp_path)
        episode_id = await h.published_episode()

        # 只造 quality 一个产物
        artifact = h.store.path_for(f"episodes/{episode_id}/algo/quality/result.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"passed": true}', encoding="utf-8")

        dataset_id = await h.accept((episode_id,))
        built = await h.builder.build(dataset_id)
        assert built.manifest_key is not None
        entry = h.manifest(built.manifest_key)["episodes"][0]

        assert set(entry["algo_artifacts"]) == {"quality"}


class TestUnpublishedEpisodesRejected:
    """未发布的不能纳入。"""

    async def test_build_fails_when_episode_not_published(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """受理到构建之间状态可能变，所以构建时还要再拦一次。"""
        h = _Harness(session, tmp_path)
        pending = await h.episode_at(EpisodeStatus.VERIFICATION_PENDING)
        dataset_id = await h.accept((pending,))

        with pytest.raises(DatasetBuildError, match="verification_pending"):
            await h.builder.build(dataset_id)

    async def test_failed_build_lands_failed_with_reason(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """失败要落库，否则调用方查到的一直是 running。"""
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((await h.episode_at(EpisodeStatus.PROCESSING),))

        with pytest.raises(DatasetBuildError):
            await h.builder.build(dataset_id)

        dataset = await h.datasets.find_by_id(dataset_id)
        assert dataset is not None
        assert dataset.status is JobStatus.FAILED
        assert dataset.failure_reason is not None
        assert dataset.manifest_key is None

    async def test_missing_episode_fails_build(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((str(uuid.uuid4()),))

        with pytest.raises(DatasetBuildError, match="不存在"):
            await h.builder.build(dataset_id)

    async def test_one_bad_episode_fails_whole_build(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """混了一条未发布的就整批失败 —— 部分成功的清单说不清缺了什么。"""
        h = _Harness(session, tmp_path)
        good = await h.published_episode()
        bad = await h.episode_at(EpisodeStatus.PROCESSING)
        dataset_id = await h.accept((good, bad))

        with pytest.raises(DatasetBuildError):
            await h.builder.build(dataset_id)

        dataset = await h.datasets.find_by_id(dataset_id)
        assert dataset is not None
        assert dataset.status is JobStatus.FAILED

    async def test_unknown_dataset_fails(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        h = _Harness(session, tmp_path)
        with pytest.raises(DatasetBuildError, match="Dataset 不存在"):
            await h.builder.build(str(uuid.uuid4()))


class TestStatusIsQueryable:
    """构建状态可查（进行中 / 完成 / 失败）。"""

    async def test_accepted_is_pending(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((await h.published_episode(),))

        dataset = await h.datasets.find_by_id(dataset_id)
        assert dataset is not None
        assert dataset.status is JobStatus.PENDING
        assert dataset.manifest_key is None

    async def test_three_states_are_distinguishable(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """pending / running / succeeded 是三个不同取值，界面据此分辨。"""
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((await h.published_episode(),))

        assert (await h.datasets.mark_running(dataset_id)).status is JobStatus.RUNNING
        built = await h.datasets.mark_succeeded(dataset_id, manifest_key="k")
        assert built.status is JobStatus.SUCCEEDED
        assert built.manifest_key == "k"

    async def test_failure_reason_is_cleared_on_success(
        self, session: AsyncSession, tmp_path: Path
    ) -> None:
        """重试成功后旧的失败原因不该还挂着。"""
        h = _Harness(session, tmp_path)
        dataset_id = await h.accept((await h.published_episode(),))

        await h.datasets.mark_failed(dataset_id, reason="上次挂了")
        built = await h.datasets.mark_succeeded(dataset_id, manifest_key="k")
        assert built.failure_reason is None
