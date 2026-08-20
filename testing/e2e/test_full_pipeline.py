"""端到端流程测试。

覆盖架构文档的完整主链路，用 in-process 方式驱动（不起 uvicorn），因此可在 CI 跑：

Agent 录制 → 上传 → 回调 Platform → 发事件 → Scheduler 消费 → 算子执行
  → 回调 Platform → 人工核验 → 标注 → 审核 → published

断言的是**状态流转与数据完整性**，不是 UI。
"""

import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rdh_contract.enums import EpisodeStatus, ReviewDecision, Role

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# 各模块以自身目录为 cwd 运行，此处为跨模块测试显式加路径。
# 注意：platform/ 会遮蔽 stdlib 的 platform 模块，故只加 app 所在目录且放在末尾。
for extra in (
    REPO_ROOT / "contract" / "src",
    REPO_ROOT / "agent" / "src",
    REPO_ROOT / "scheduler" / "src",
    REPO_ROOT / "platform",
):
    if str(extra) not in sys.path:
        sys.path.append(str(extra))


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的运行目录，避免测试之间互相污染。"""
    monkeypatch.setenv("RDH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'platform.db'}")
    monkeypatch.setenv("RDH_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("RDH_EVENT_QUEUE_DIR", str(tmp_path / "queue"))
    monkeypatch.setenv("RDH_DLQ_DIR", str(tmp_path / "dlq"))
    monkeypatch.setenv("RDH_PROCESSED_DIR", str(tmp_path / "processed"))
    monkeypatch.setenv("RDH_RECORDING_DIR", str(tmp_path / "recordings"))
    monkeypatch.setenv("RDH_STATE_DB_PATH", str(tmp_path / "agent-state.sqlite"))
    return tmp_path


@pytest.mark.e2e
async def test_full_pipeline_reaches_published(runtime: Path) -> None:
    """完整主链路走到 published。"""
    # 清掉配置缓存，让新环境变量生效
    from app.core.config import get_settings as platform_settings
    from app.db.session import get_engine, get_session_factory, init_schema

    platform_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    settings = platform_settings()
    settings.ensure_dirs()
    await init_schema()

    from app.core.security import hash_password
    from app.repositories.annotation import AnnotationRepository
    from app.repositories.episode import EpisodeRepository
    from app.repositories.task import TaskRepository
    from app.repositories.user import UserRepository
    from app.services.callbacks import CallbackService
    from app.services.episode_lifecycle import EpisodeLifecycleService
    from app.services.event_publisher import FileQueuePublisher
    from app.services.object_store import LocalObjectStore
    from app.services.review import ReviewService
    from rdh_contract.schemas import (
        AnnotationSubmit,
        ReviewResult,
        Segment,
        TaskCreate,
        TaskRequirement,
        VerifyResult,
    )
    from rdh_contract.schemas.agent import UploadCallback

    publisher = FileQueuePublisher(settings.event_queue_dir)
    store = LocalObjectStore(settings.object_store_root)
    factory = get_session_factory()

    # ---- 准备：用户与任务 ----
    async with factory() as session:
        users = UserRepository(session)
        await users.create(
            user_id=str(uuid.uuid4()),
            username="operator",
            display_name="操作员",
            password_hash=hash_password("local-pass"),
            roles=(Role.ADMIN, Role.VERIFIER, Role.ANNOTATOR, Role.REVIEWER),
        )
        tasks = TaskRepository(session)
        task = await tasks.create(
            task_id=str(uuid.uuid4()),
            payload=TaskCreate(
                name="厨房抓取",
                requirement=TaskRequirement(
                    robot_model="rm-75-6f",
                    scene="kitchen",
                    required_topics=("/camera/front/image_raw", "/joint_states"),
                    min_duration_ms=1000,
                    max_duration_ms=60000,
                    target_episode_count=1,
                ),
            ),
            created_by="operator",
        )
        await session.commit()

    # ---- 1. Agent 侧：录制 ----
    from agent.recorder.mcap_writer import record_simulated_episode

    async with factory() as session:
        episodes = EpisodeRepository(session)
        episode = await episodes.create(
            episode_id=str(uuid.uuid4()),
            task_id=task.task_id,
            agent_id="agent-e2e",
            status=EpisodeStatus.RECORDING,
            robot_model="rm-75-6f",
            scene="kitchen",
        )
        await session.commit()
    episode_id = episode.episode_id

    recording_dir = runtime / "recordings"
    recording_dir.mkdir(parents=True, exist_ok=True)
    local_path = recording_dir / f"{episode_id}.mcap"
    stats = record_simulated_episode(local_path, episode_id=episode_id, duration_ms=6000)
    assert stats.message_count > 0
    assert len(stats.topics) == 5

    # ---- 2. Agent 侧：分片上传（交互②）----
    from agent.uploader.chunked import LocalChunkUploader

    uploader = LocalChunkUploader(object_store_root=settings.object_store_root, chunk_size=8192)
    object_key = f"episodes/{episode_id}/raw.mcap"
    upload = uploader.upload(source=local_path, object_key=object_key)
    assert upload.complete
    assert upload.total_parts > 1, "分片数应大于 1，否则测不到多片逻辑"

    # ---- 3. 上传回调（交互③）→ 触发事件（交互⑤）----
    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        await lifecycle.transition(episode_id, target=EpisodeStatus.UPLOADING)
        await session.commit()

        callbacks = CallbackService(
            lifecycle=lifecycle,
            episodes=episodes,
            tasks=TaskRepository(session),
            object_store=store,
        )
        outcome = await callbacks.handle_upload_complete(
            UploadCallback(
                episode_id=episode_id,
                object_key=object_key,
                size_bytes=upload.size_bytes,
                checksum=upload.checksum,
                duration_ms=stats.duration_ms,
                recorded_topics=stats.topics,
                completed_at=datetime.now(UTC),
            )
        )
        await session.commit()
    # 上传回调落定的是 processing：事件投递即视为进入处理，Scheduler 只上报结果、
    # 不改 Platform 状态，所以 uploaded → processing 由 Platform 自己在发事件时完成。
    assert outcome.episode.status is EpisodeStatus.PROCESSING
    assert publisher.pending_count("ingest") == 1, "应发出一条 episode.uploaded"

    # ---- 4. Scheduler 消费（交互⑥）+ 算子执行（交互⑦）----
    from scheduler.config import get_settings as scheduler_settings

    scheduler_settings.cache_clear()
    sched = scheduler_settings()
    sched.ensure_dirs()

    from scheduler.consumers.queue import FileQueueConsumer
    from scheduler.k8s.job_builder import build_spec
    from scheduler.k8s.runner import SubprocessRunner

    consumer = FileQueueConsumer(
        queue_dir=sched.event_queue_dir,
        dlq_dir=sched.dlq_dir,
        processed_dir=sched.processed_dir,
        queue_name="ingest",
    )
    event = consumer.fetch()
    assert event is not None, "Scheduler 未取到事件"
    assert event.routing_key == "episode.uploaded"
    # 消费者按契约反序列化，这里收窄类型以断言业务字段
    from rdh_contract.events import EpisodeUploaded

    assert isinstance(event.payload, EpisodeUploaded)
    assert event.payload.episode_id == episode_id

    # 这里原本手动补 start_processing —— 那是在替生产代码打补丁，掩盖了「没人做
    # uploaded → processing」这个真实缺陷。现在 Platform 在发事件时自己推进，
    # 测试只需确认状态已经就位。
    async with factory() as session:
        stored = await EpisodeRepository(session).find_by_id(episode_id)
        assert stored is not None
        assert stored.status is EpisodeStatus.PROCESSING

    runner = SubprocessRunner(
        algo_root=REPO_ROOT / "algo",
        object_store_root=sched.object_store_root,
        timeout_seconds=120,
    )
    from rdh_contract.enums import AlgoOperator, JobStatus

    results = []
    for operator in AlgoOperator:
        spec = build_spec(
            job_id=str(uuid.uuid4()),
            episode_id=episode_id,
            operator=operator,
            input_object_key=object_key,
            registry="robotdatahub",
            model_version="v0.1.0",
            timeout_seconds=120,
            ttl_seconds=300,
        )
        results.append(await runner.run(spec))

    failures = [
        (r.operator.value, r.error_message) for r in results if r.status is not JobStatus.SUCCEEDED
    ]
    assert not failures, f"算子执行失败：{failures}"
    consumer.ack(event)

    preannotate = next(r for r in results if r.operator is AlgoOperator.PREANNOTATE)
    assert len(preannotate.segments) >= 2, "预标注应切出多个分段"
    quality = next(r for r in results if r.operator is AlgoOperator.QUALITY)
    assert quality.quality is not None

    # ---- 5. 算子结果回调（交互⑧）----
    from rdh_contract.schemas.scheduler import AlgoResultCallback

    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        callbacks = CallbackService(
            lifecycle=lifecycle,
            episodes=episodes,
            tasks=TaskRepository(session),
            object_store=store,
        )
        outcome = await callbacks.handle_algo_result(
            AlgoResultCallback(
                episode_id=episode_id,
                results=tuple(results),
                pipeline_complete=True,
                reported_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert outcome.episode.status is EpisodeStatus.VERIFICATION_PENDING
    assert len(outcome.episode.segments) >= 2, "算子分段应已落库"

    # ---- 6. 人工核验（交互④）----
    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        review = ReviewService(
            lifecycle=lifecycle,
            annotations=AnnotationRepository(session),
            episodes=episodes,
            tasks=TaskRepository(session),
        )
        outcome = await review.submit_verification(
            VerifyResult(
                episode_id=episode_id,
                decision=ReviewDecision.APPROVE,
                checked_topics=stats.topics,
                verified_by="operator",
                verified_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert outcome.episode.status is EpisodeStatus.ANNOTATION_PROCESSING

    # ---- 6b. 送标处理回调（Scheduler，本阶段不跑算子）----
    # 质检通过后不再直连 annotation_pending，中间多了这个异步环节。
    from rdh_contract.schemas.scheduler import AnnotationProcessingCallback

    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        callbacks = CallbackService(
            lifecycle=lifecycle,
            episodes=episodes,
            tasks=TaskRepository(session),
            object_store=store,
        )
        outcome = await callbacks.handle_annotation_processing(
            AnnotationProcessingCallback(
                episode_id=episode_id,
                succeeded=True,
                reported_at=datetime.now(UTC),
            )
        )
        await session.commit()
    assert outcome.episode.status is EpisodeStatus.ANNOTATION_PENDING

    # ---- 7. 人工标注 ----
    edited = tuple(
        Segment(
            segment_id=s.segment_id,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            action_label=s.action_label or "manual",
            description="人工确认",
            source=None,  # 人工修改后不再是算子产出
            confidence=None,
        )
        for s in outcome.episode.segments
    )
    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        review = ReviewService(
            lifecycle=lifecycle,
            annotations=AnnotationRepository(session),
            episodes=episodes,
            tasks=TaskRepository(session),
        )
        annotation, outcome = await review.submit_annotation(
            AnnotationSubmit(episode_id=episode_id, segments=edited, notes="e2e"),
            annotated_by="operator",
        )
        await session.commit()
    assert outcome.episode.status is EpisodeStatus.ANNOTATION_REVIEW

    # ---- 8. 审核通过 → published ----
    async with factory() as session:
        episodes = EpisodeRepository(session)
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        review = ReviewService(
            lifecycle=lifecycle,
            annotations=AnnotationRepository(session),
            episodes=episodes,
            tasks=TaskRepository(session),
        )
        outcome = await review.submit_review(
            ReviewResult(
                episode_id=episode_id,
                decision=ReviewDecision.APPROVE,
                reviewed_by="operator",
                reviewed_at=datetime.now(UTC),
            )
        )
        await session.commit()

    assert outcome.episode.status is EpisodeStatus.PUBLISHED
    assert publisher.pending_count("tool") == 1, "应发出 annotation.approved"

    # 任务计数应已累加
    async with factory() as session:
        refreshed = await TaskRepository(session).find_by_id(task.task_id)
    assert refreshed is not None
    assert refreshed.published_count == 1


@pytest.mark.e2e
async def test_verification_reject_terminates_episode(runtime: Path) -> None:
    """核验打回 → rejected 终态，并发出 episode.rejected 事件。"""
    from app.core.config import get_settings as platform_settings
    from app.db.session import get_engine, get_session_factory, init_schema

    platform_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    settings = platform_settings()
    settings.ensure_dirs()
    await init_schema()

    from app.repositories.episode import EpisodeRepository
    from app.services.episode_lifecycle import EpisodeLifecycleService
    from app.services.event_publisher import FileQueuePublisher
    from rdh_contract.state_machine import is_terminal

    publisher = FileQueuePublisher(settings.event_queue_dir)
    factory = get_session_factory()
    episode_id = str(uuid.uuid4())

    async with factory() as session:
        episodes = EpisodeRepository(session)
        await episodes.create(
            episode_id=episode_id,
            task_id="t-reject",
            agent_id="agent-e2e",
            status=EpisodeStatus.VERIFICATION_PENDING,
        )
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
        outcome = await lifecycle.reject(episode_id, reason="画面严重模糊", rejected_by="operator")
        await session.commit()

    assert outcome.episode.status is EpisodeStatus.REJECTED
    assert is_terminal(outcome.episode.status)
    assert outcome.episode.reject_reason == "画面严重模糊"
    assert publisher.pending_count("notify") == 1


@pytest.mark.e2e
async def test_illegal_transition_is_rejected(runtime: Path) -> None:
    """终态 Episode 不可复活 —— 防止后台任务覆盖人工判定。"""
    from app.core.config import get_settings as platform_settings
    from app.db.session import get_engine, get_session_factory, init_schema

    platform_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    settings = platform_settings()
    settings.ensure_dirs()
    await init_schema()

    from app.repositories.episode import EpisodeRepository
    from app.services.episode_lifecycle import EpisodeLifecycleService
    from app.services.event_publisher import NullPublisher
    from rdh_contract.state_machine import InvalidTransitionError

    factory = get_session_factory()
    episode_id = str(uuid.uuid4())

    async with factory() as session:
        episodes = EpisodeRepository(session)
        await episodes.create(
            episode_id=episode_id,
            task_id="t-illegal",
            agent_id="agent-e2e",
            status=EpisodeStatus.PUBLISHED,
        )
        await session.commit()
        lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=NullPublisher())
        with pytest.raises(InvalidTransitionError):
            await lifecycle.transition(episode_id, target=EpisodeStatus.PROCESSING)


@pytest.mark.e2e
async def test_upload_callback_replay_is_idempotent(runtime: Path) -> None:
    """重复上传回调不报错 —— RabbitMQ 与 HTTP 重试都会造成重放。"""
    from app.core.config import get_settings as platform_settings
    from app.db.session import get_engine, get_session_factory, init_schema

    platform_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    settings = platform_settings()
    settings.ensure_dirs()
    await init_schema()

    from app.repositories.episode import EpisodeRepository
    from app.repositories.task import TaskRepository
    from app.services.callbacks import CallbackService
    from app.services.episode_lifecycle import EpisodeLifecycleService
    from app.services.event_publisher import FileQueuePublisher
    from app.services.object_store import LocalObjectStore
    from rdh_contract.schemas import TaskCreate, TaskRequirement
    from rdh_contract.schemas.agent import UploadCallback

    publisher = FileQueuePublisher(settings.event_queue_dir)
    factory = get_session_factory()
    episode_id = str(uuid.uuid4())

    callback = UploadCallback(
        episode_id=episode_id,
        object_key=f"episodes/{episode_id}/raw.mcap",
        size_bytes=1024,
        checksum="deadbeef",
        duration_ms=5000,
        recorded_topics=("/joint_states",),
        completed_at=datetime.now(UTC),
    )

    async with factory() as session:
        # 回调会累加任务计数，因此任务必须存在
        task = await TaskRepository(session).create(
            task_id=str(uuid.uuid4()),
            payload=TaskCreate(
                name="重放测试",
                requirement=TaskRequirement(
                    robot_model="rm-75-6f",
                    scene="kitchen",
                    required_topics=("/joint_states",),
                    min_duration_ms=1000,
                    max_duration_ms=60000,
                    target_episode_count=1,
                ),
            ),
            created_by="operator",
        )
        episodes = EpisodeRepository(session)
        await episodes.create(
            episode_id=episode_id,
            task_id=task.task_id,
            agent_id="agent-e2e",
            status=EpisodeStatus.UPLOADING,
        )
        await session.commit()

        service = CallbackService(
            lifecycle=EpisodeLifecycleService(episodes=episodes, publisher=publisher),
            episodes=episodes,
            tasks=TaskRepository(session),
            object_store=LocalObjectStore(settings.object_store_root),
        )
        # 对象不存在时跳过 checksum 校验（这里只测幂等）
        first = await service.handle_upload_complete(callback, verify_checksum=False)
        await session.commit()
        second = await service.handle_upload_complete(callback, verify_checksum=False)
        await session.commit()

    assert first.changed is True
    assert second.changed is False, "重放应识别为已处理而非报错"
    assert publisher.pending_count("ingest") == 1, "重放不应重复发事件"


@pytest.mark.e2e
async def test_agent_recovery_resumes_partial_upload(runtime: Path) -> None:
    """断电恢复：只补缺口分片，不重传已完成的。"""
    from agent.config import get_settings as agent_settings
    from agent.recorder.mcap_writer import record_simulated_episode
    from agent.store.sqlite import StateStore
    from agent.uploader.chunked import LocalChunkUploader

    agent_settings.cache_clear()
    settings = agent_settings()
    settings.ensure_dirs()

    store = StateStore(settings.state_db_path)
    episode_id = str(uuid.uuid4())
    local_path = settings.recording_dir / f"{episode_id}.mcap"
    stats = record_simulated_episode(local_path, episode_id=episode_id, duration_ms=4000)

    store.record_episode(episode_id=episode_id, task_id="t-recover", local_path=local_path)
    store.finish_recording(
        episode_id,
        duration_ms=stats.duration_ms,
        size_bytes=stats.size_bytes,
        checksum=stats.checksum,
        recorded_topics=stats.topics,
    )

    uploader = LocalChunkUploader(object_store_root=settings.object_store_root, chunk_size=4096)
    object_key = f"episodes/{episode_id}/raw.mcap"

    # 模拟传了一半就断电
    from agent.uploader.chunked import plan_parts

    total = plan_parts(stats.size_bytes, 4096)
    assert total >= 3, "文件应足够大以产生多个分片"
    store.start_upload(episode_id, object_key=object_key, total_parts=total)
    store.mark_part_uploaded(episode_id, 1)

    record = store.get(episode_id)
    assert record is not None
    assert record.needs_upload
    assert 1 not in record.missing_parts
    assert len(record.missing_parts) == total - 1

    # 恢复：只补缺口
    uploaded_during_resume: list[int] = []
    outcome = uploader.upload(
        source=local_path,
        object_key=object_key,
        already_uploaded=record.uploaded_parts,
        on_part_done=uploaded_during_resume.append,
    )
    assert outcome.complete
    assert 1 not in uploaded_during_resume, "已完成分片不应重传"
    assert len(uploaded_during_resume) == total - 1

    store.complete_upload(episode_id)
    store.mark_callback_done(episode_id)
    assert store.unfinished() == ()


@pytest.mark.e2e
async def test_scheduler_sends_bad_event_to_dlq(runtime: Path) -> None:
    """不合契约的消息进死信，不阻塞队列。"""
    from scheduler.config import get_settings as scheduler_settings

    scheduler_settings.cache_clear()
    sched = scheduler_settings()
    sched.ensure_dirs()

    from scheduler.consumers.queue import FileQueueConsumer

    queue_path = sched.event_queue_dir / "ingest"
    queue_path.mkdir(parents=True, exist_ok=True)
    (queue_path / "20260817000000-bad.json").write_text(
        '{"routing_key": "episode.uploaded", "event_id": "bad-1", "payload": {"nope": 1}}',
        encoding="utf-8",
    )

    consumer = FileQueueConsumer(
        queue_dir=sched.event_queue_dir,
        dlq_dir=sched.dlq_dir,
        processed_dir=sched.processed_dir,
        queue_name="ingest",
    )
    assert consumer.fetch() is None, "不合契约的消息不应被取出处理"
    assert consumer.dlq_count() == 1


def test_event_loop_policy_available() -> None:
    """确保 asyncio 可用（早期环境问题会让上面全部 e2e 无法运行）。"""
    assert asyncio.get_event_loop_policy() is not None
