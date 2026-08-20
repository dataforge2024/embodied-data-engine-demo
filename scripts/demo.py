"""端到端 MVP demo：把架构文档的 8 条交互真跑一遍。

不需要 PostgreSQL / MinIO / RabbitMQ / K8s —— 用本地替身（SQLite / 本地目录 /
文件队列 / 子进程），但**每一步的接口与状态流转都是真实的**。

跑法::

    make demo            # 或 uv run --project testing python scripts/demo.py

流程（编号对应架构文档第一节的核心交互）：

1. Admin 建采集任务并分派给 Agent
2. Agent 登记 Episode → 录制 MCAP
3. Agent 分片上传（交互②）→ 上传完成回调（交互③）
4. Platform 发 ``episode.uploaded``（交互⑤）
5. Scheduler 消费事件（交互⑥）→ 跑 4 个算子（交互⑦）
6. Scheduler 回调 Platform（交互⑧）→ Episode 进核验队列
7. 人工核验通过（交互④）→ 标注 → 审核通过 → published
8. 打印全链路状态轨迹与产物统计
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 各模块平时以自身目录为 cwd 运行；demo 要跨模块驱动，显式加路径。
# platform/ 会遮蔽 stdlib 的 platform 模块，因此放在末尾且只加它的父目录。
for extra in (
    REPO_ROOT / "contract" / "src",
    REPO_ROOT / "agent" / "src",
    REPO_ROOT / "scheduler" / "src",
    REPO_ROOT / "platform",
):
    sys.path.append(str(extra))

# demo 与 `make dev` 共用同一个运行目录，否则 demo 跑完 Web UI 里看不到数据。
# 与 platform/app/core/config.py、agent/src/agent/config.py 的 DEFAULT_RUNTIME_DIR 一致。
RUNTIME = REPO_ROOT / ".runtime"

STEP_WIDTH = 72


def banner(text: str) -> None:
    """打印阶段标题。"""
    print(f"\n{'━' * STEP_WIDTH}\n▶ {text}\n{'━' * STEP_WIDTH}")


def step(text: str) -> None:
    """打印步骤。"""
    print(f"  · {text}")


def ok(text: str) -> None:
    """打印成功。"""
    print(f"  ✓ {text}")


async def main() -> int:  # noqa: PLR0915 — demo 是线性叙事，拆开反而难读
    """跑完整链路。"""
    # ---- 干净起步：每次 demo 重置运行目录，避免上次残留干扰断言 ----
    # 注意这会清掉 `make dev` 期间积累的本地数据，等同 `make clean-runtime`。
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)

    import os

    os.environ["RDH_DATABASE_URL"] = f"sqlite+aiosqlite:///{RUNTIME / 'platform.db'}"
    os.environ["RDH_OBJECT_STORE_ROOT"] = str(RUNTIME / "objects")
    os.environ["RDH_EVENT_QUEUE_DIR"] = str(RUNTIME / "queue")
    os.environ["RDH_DLQ_DIR"] = str(RUNTIME / "dlq")
    os.environ["RDH_PROCESSED_DIR"] = str(RUNTIME / "processed")
    os.environ["RDH_RECORDING_DIR"] = str(RUNTIME / "recordings")
    os.environ["RDH_STATE_DB_PATH"] = str(RUNTIME / "agent-state.sqlite")

    from rdh_contract import __version__ as contract_version
    from rdh_contract.enums import AlgoOperator, EpisodeStatus, JobStatus, ReviewDecision, Role
    from rdh_contract.schemas import (
        AnnotationSubmit,
        ReviewResult,
        Segment,
        TaskCreate,
        TaskRequirement,
        TransitionActor,
        VerifyResult,
    )
    from rdh_contract.schemas.agent import UploadCallback
    from rdh_contract.schemas.scheduler import AlgoResultCallback, AnnotationProcessingCallback
    from rdh_contract.state_machine import is_terminal

    banner(f"RobotDataHub MVP Demo · 契约 v{contract_version}")
    step(f"运行目录 {RUNTIME.relative_to(REPO_ROOT)}")

    # ---- Platform 初始化 ----
    from app.core.config import get_settings
    from app.core.security import hash_password
    from app.db.session import get_engine, get_session_factory, init_schema

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()
    await init_schema()

    from app.repositories.agent_node import AgentNodeRepository
    from app.repositories.algo_job_run import AlgoJobRunRepository
    from app.repositories.annotation import AnnotationRepository
    from app.repositories.dataset import DatasetRepository
    from app.repositories.episode import EpisodeRepository
    from app.repositories.task import TaskRepository
    from app.repositories.transition import TransitionRepository
    from app.repositories.user import UserRepository
    from app.services.callbacks import CallbackService
    from app.services.dataset_builder import DatasetBuilder
    from app.services.episode_lifecycle import EpisodeLifecycleService
    from app.services.object_store import LocalObjectStore
    from app.services.review import ReviewService
    from app.services.task import TaskService

    # Scheduler 的配置要先就位：队列适配层两个后端都要用到它
    from scheduler.config import get_settings as scheduler_settings

    scheduler_settings.cache_clear()
    sched = scheduler_settings()
    sched.ensure_dirs()

    from demo_queue import build_demo_queue

    queues = build_demo_queue(
        backend=settings.queue_backend,
        amqp_url=settings.amqp_url,
        queue_dir=settings.event_queue_dir,
        sched=sched,
    )
    await queues.purge()  # broker 里可能留着上次跑的消息
    step(f"替身：{queues.substitute_note}")

    publisher = queues.publisher
    object_store = LocalObjectStore(settings.object_store_root)
    factory = get_session_factory()
    status_trail: list[str] = []

    def make_lifecycle(session: object) -> EpisodeLifecycleService:
        return EpisodeLifecycleService(
            episodes=EpisodeRepository(session),  # type: ignore[arg-type]
            publisher=publisher,
        )

    # ============ 阶段 1：Admin 建任务并分派 ============
    banner("阶段 1 · Admin 建采集任务并分派给 Agent（交互①）")

    async with factory() as session:
        users = UserRepository(session)
        admin = await users.create(
            user_id=str(uuid.uuid4()),
            username="admin",
            display_name="管理员",
            password_hash=hash_password("demo-only-pass"),
            roles=(Role.ADMIN,),
        )
        # 采集员。后续核验/标注/审核阶段复用它的 user_id —— 那些是直接调 service 层，
        # 不过路由守卫，所以此处不需要给它 verifier/annotator/reviewer 角色。
        operator_user = await users.create(
            user_id=str(uuid.uuid4()),
            username="recorder",
            display_name="采集员",
            password_hash=hash_password("demo-only-pass"),
            roles=(Role.RECORDER,),
        )
        agents = AgentNodeRepository(session, heartbeat_timeout_seconds=45)
        await agents.register(agent_id="agent-demo-01", hostname="collect-pc-01", version="0.1.0")

        task_service = TaskService(tasks=TaskRepository(session), agents=agents)
        task = await task_service.create_task(
            TaskCreate(
                name="厨房抓取-杯子",
                description="从台面抓取杯子并放入水槽",
                requirement=TaskRequirement(
                    robot_model="rm-75-6f",
                    scene="kitchen",
                    required_topics=("/camera/front/image_raw", "/joint_states", "/gripper/state"),
                    min_duration_ms=3000,
                    max_duration_ms=30000,
                    target_episode_count=1,
                ),
            ),
            created_by=admin.user_id,
        )
        _, assignment = await task_service.assign(
            task.task_id, agent_id="agent-demo-01", assigned_by=admin.user_id
        )
        await session.commit()

    ok(f"任务已建 {task.name} · 目标 {task.requirement.target_episode_count} 条")
    ok(f"已分派给 {assignment.agent_id}（生产环境经 WS 推送 down.task_push）")

    # ============ 阶段 2：Agent 录制 ============
    banner("阶段 2 · Agent 登记 Episode 并录制 MCAP")

    from agent.recorder.mcap_writer import record_simulated_episode

    async with factory() as session:
        episode = await EpisodeRepository(session).create(
            episode_id=str(uuid.uuid4()),
            task_id=task.task_id,
            agent_id="agent-demo-01",
            status=EpisodeStatus.RECORDING,
            recorded_by=operator_user.user_id,
            robot_model="rm-75-6f",
            scene="kitchen",
        )
        await session.commit()
    episode_id = episode.episode_id
    status_trail.append(episode.status.value)
    ok(f"Episode 已登记 {episode_id[:8]} · 状态 {episode.status.value}")

    recording_dir = RUNTIME / "recordings"
    recording_dir.mkdir(parents=True, exist_ok=True)
    local_path = recording_dir / f"{episode_id}.mcap"
    stats = record_simulated_episode(local_path, episode_id=episode_id, duration_ms=8000)
    ok(
        f"录制完成 {stats.message_count} 条消息 · {len(stats.topics)} 个 topic · "
        f"{stats.size_bytes / 1024:.1f} KiB · {stats.duration_ms}ms"
    )
    for topic in stats.topics:
        step(f"topic {topic}")

    # ============ 阶段 3：分片上传 + 回调 ============
    banner("阶段 3 · 分片上传（交互②）与上传完成回调（交互③）")

    from agent.store.sqlite import StateStore
    from agent.uploader.chunked import LocalChunkUploader

    # 走 Agent 自己的配置，而不是从 Platform 的路径推算 —— 两边同读 RDH_STATE_DB_PATH。
    from agent.config import get_settings as agent_settings

    agent_settings.cache_clear()
    agent_conf = agent_settings()
    agent_conf.ensure_dirs()
    store = StateStore(agent_conf.state_db_path)
    store.record_episode(episode_id=episode_id, task_id=task.task_id, local_path=local_path)
    store.finish_recording(
        episode_id,
        duration_ms=stats.duration_ms,
        size_bytes=stats.size_bytes,
        checksum=stats.checksum,
        recorded_topics=stats.topics,
    )

    async with factory() as session:
        outcome = await make_lifecycle(session).transition(
            episode_id,
            target=EpisodeStatus.UPLOADING,
            actor=TransitionActor(actor_type="system", system_component="agent_report"),
        )
        await session.commit()
    status_trail.append(outcome.episode.status.value)
    ok(f"状态推进 → {outcome.episode.status.value}")

    uploader = LocalChunkUploader(
        object_store_root=settings.object_store_root, chunk_size=16 * 1024
    )
    object_key = object_store.build_object_key(episode_id)
    parts_seen: list[int] = []
    upload = uploader.upload(
        source=local_path,
        object_key=object_key,
        on_part_done=lambda part: parts_seen.append(part),
    )
    store.start_upload(episode_id, object_key=object_key, total_parts=upload.total_parts)
    for part in upload.uploaded_parts:
        store.mark_part_uploaded(episode_id, part)
    store.complete_upload(episode_id)
    ok(f"上传完成 {upload.total_parts} 个分片 · 每片落库一次（断点续传的前提）")
    step(f"对象键 {object_key}")
    step(f"checksum {upload.checksum[:16]}…")

    async with factory() as session:
        episodes_repo = EpisodeRepository(session)
        callbacks = CallbackService(
            lifecycle=make_lifecycle(session),
            episodes=episodes_repo,
            tasks=TaskRepository(session),
            object_store=object_store,
            algo_job_runs=AlgoJobRunRepository(session),
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
    store.mark_callback_done(episode_id)
    status_trail.append(outcome.episode.status.value)
    ok("服务端独立重算 checksum 通过（不信任 Agent 的声明）")
    ok(f"状态推进 → {outcome.episode.status.value}")
    ok(f"已发布 episode.uploaded（交互⑤）· ingest 队列深度 {await queues.depth('ingest')}")

    # ============ 阶段 4：Scheduler 消费 + 算子 ============
    banner("阶段 4 · Scheduler 消费事件（交互⑥）并执行算子（交互⑦）")

    from scheduler.k8s.job_builder import build_job_manifest, build_spec
    from scheduler.k8s.runner import SubprocessRunner

    event = await queues.fetch("ingest")
    if event is None:
        print("✗ Scheduler 未取到事件，链路断裂")
        return 1
    ok(f"ingest-worker 取到 {event.routing_key} · event_id {event.event_id[:8]}")

    # uploaded → processing 由 Platform 在发出 episode.uploaded 时自己完成，
    # demo 不再代劳 —— 早先这里手动补一跳，掩盖了生产链路没人做这一跳的缺陷。
    async with factory() as session:
        stored = await EpisodeRepository(session).find_by_id(episode_id)
        assert stored is not None
    status_trail.append(stored.status.value)
    ok(f"状态已就位 → {stored.status.value}（Platform 发事件时推进）")

    runner = SubprocessRunner(
        algo_root=REPO_ROOT / "algo",
        object_store_root=sched.object_store_root,
        timeout_seconds=sched.algo_job_timeout_seconds,
    )

    results = []
    for operator in AlgoOperator:
        spec = build_spec(
            job_id=str(uuid.uuid4()),
            episode_id=episode_id,
            operator=operator,
            input_object_key=object_key,
            registry=sched.algo_image_registry,
            model_version=sched.algo_model_version,
            timeout_seconds=sched.algo_job_timeout_seconds,
            ttl_seconds=sched.algo_job_ttl_seconds,
        )
        # manifest 构造是真实的，只是本地不提交给集群
        manifest = build_job_manifest(spec)
        result = await runner.run(spec)
        results.append(result)

        gpu = manifest["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"].get(
            "nvidia.com/gpu", "0"
        )
        mark = "✓" if result.status is JobStatus.SUCCEEDED else "✗"
        detail = {
            AlgoOperator.PREANNOTATE: f"{len(result.segments)} 个分段",
            AlgoOperator.KEYFRAME: f"{len(result.key_frames)} 个关键帧",
            AlgoOperator.QUALITY: (
                f"质检{'通过' if result.quality and result.quality.passed else '未通过'}"
                f"（{len(result.quality.issues) if result.quality else 0} 项问题）"
            ),
            AlgoOperator.ANOMALY: f"{len(result.anomalies)} 个异常",
        }[operator]
        print(
            f"  {mark} {operator.value:12s} gpu={gpu} "
            f"{result.duration_seconds:.2f}s  {detail}"
            f"{'  ' + (result.error_message or '') if result.error_message else ''}"
        )
        step(f"K8s Job 名 {manifest['metadata']['name'][:52]} · TTL {spec.ttl_seconds}s")

    await queues.ack("ingest", event)
    ok(
        f"事件已 ack · 已处理 {await queues.processed_count()} 条 · "
        f"死信 {await queues.dlq_count()} 条"
    )

    # ============ 阶段 5：结果回调 ============
    banner("阶段 5 · Scheduler 回调 Platform（交互⑧）")

    async with factory() as session:
        episodes_repo = EpisodeRepository(session)
        callbacks = CallbackService(
            lifecycle=make_lifecycle(session),
            episodes=episodes_repo,
            tasks=TaskRepository(session),
            object_store=object_store,
            algo_job_runs=AlgoJobRunRepository(session),
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
    status_trail.append(outcome.episode.status.value)
    ok(f"算子产物已落库 · {len(outcome.episode.segments)} 个分段 · "
       f"{len(outcome.episode.key_frames)} 个关键帧")
    ok(f"状态推进 → {outcome.episode.status.value}（进核验队列）")

    # ============ 阶段 6：人工环节 ============
    banner("阶段 6 · 人工核验 → 标注 → 审核（交互④，Tool 侧操作）")

    def build_review(session: object) -> ReviewService:
        return ReviewService(
            lifecycle=make_lifecycle(session),
            annotations=AnnotationRepository(session),  # type: ignore[arg-type]
            episodes=EpisodeRepository(session),  # type: ignore[arg-type]
            tasks=TaskRepository(session),  # type: ignore[arg-type]
        )

    async with factory() as session:
        review = build_review(session)
        queue, total = await review.verification_queue()
        step(f"核验队列 {total} 条")
        outcome = await review.submit_verification(
            VerifyResult(
                episode_id=episode_id,
                decision=ReviewDecision.APPROVE,
                checked_topics=stats.topics,
                verified_by=operator_user.user_id,
                verified_at=datetime.now(UTC),
            )
        )
        await session.commit()
    status_trail.append(outcome.episode.status.value)
    ok(f"核验通过 → {outcome.episode.status.value}")

    # 送标处理：质检通过后不再直连 annotation_pending，中间是这个异步环节。
    # 本阶段不跑算子（design.md 第 2 节），所以只是转个状态、没有可见耗时。
    async with factory() as session:
        callbacks = CallbackService(
            lifecycle=make_lifecycle(session),
            episodes=EpisodeRepository(session),
            tasks=TaskRepository(session),
            object_store=object_store,
            algo_job_runs=AlgoJobRunRepository(session),
        )
        outcome = await callbacks.handle_annotation_processing(
            AnnotationProcessingCallback(
                episode_id=episode_id,
                succeeded=True,
                reported_at=datetime.now(UTC),
            )
        )
        await session.commit()
    status_trail.append(outcome.episode.status.value)
    ok(f"送标处理完成 → {outcome.episode.status.value}")

    # 标注人在预标注分段上修改，而不是从零画
    algo_segments = outcome.episode.segments
    edited = tuple(
        Segment(
            segment_id=segment.segment_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            action_label=segment.action_label or "manual",
            description=f"人工确认：{segment.action_label or '动作'}",
            source=None,  # 人工改过就不再是算子产出
            confidence=None,
        )
        for segment in algo_segments
    )

    async with factory() as session:
        review = build_review(session)
        annotation, outcome = await review.submit_annotation(
            AnnotationSubmit(
                episode_id=episode_id, segments=edited, notes="demo 自动标注"
            ),
            annotated_by=operator_user.user_id,
        )
        await session.commit()
    status_trail.append(outcome.episode.status.value)
    ok(f"标注提交 {len(annotation.segments)} 个分段（基于预标注修改）→ {outcome.episode.status.value}")

    async with factory() as session:
        review = build_review(session)
        outcome = await review.submit_review(
            ReviewResult(
                episode_id=episode_id,
                decision=ReviewDecision.APPROVE,
                reviewed_by=operator_user.user_id,
                reviewed_at=datetime.now(UTC),
            )
        )
        await session.commit()
    status_trail.append(outcome.episode.status.value)
    ok(f"审核通过 → {outcome.episode.status.value}")
    ok(f"已发布 annotation.approved · tool 队列深度 {await queues.depth('tool')}")

    # ============ 阶段 7：tool-worker 消费 ============
    banner("阶段 7 · tool-worker 消费 annotation.approved")

    tool_event = await queues.fetch("tool")
    if tool_event is not None:
        ok(f"tool-worker 取到 {tool_event.routing_key}（真实实现在此并入训练集）")
        await queues.ack("tool", tool_event)

    # ============ 阶段 7b：导出训练集 ============
    banner("阶段 7b · 导出训练集（产出 manifest 清单）")

    async with factory() as session:
        datasets = DatasetRepository(session)
        dataset = await datasets.create(
            dataset_id=str(uuid.uuid4()),
            episode_ids=(episode_id,),
            output_format="lerobot",
            requested_by=operator_user.user_id,
        )
        await session.commit()
    step(f"构建已受理 {dataset.dataset_id[:8]} · 状态 {dataset.status.value}")

    async with factory() as session:
        builder = DatasetBuilder(
            datasets=DatasetRepository(session),
            episodes=EpisodeRepository(session),
            object_store=object_store,
        )
        built = await builder.build(dataset.dataset_id)
        await session.commit()
    ok(f"构建完成 · 状态 {built.status.value} · 清单 {built.manifest_key}")

    # 清单是 JSON，直接读出来看几个关键字段 —— 演示时「导出」要有东西可看
    assert built.manifest_key is not None
    manifest = json.loads(
        object_store.path_for(built.manifest_key).read_text(encoding="utf-8")
    )
    step(
        f"清单含 {manifest['episode_count']} 条 Episode · "
        f"{manifest['segment_count']} 个人工分段"
    )

    # ============ 阶段 8：汇总 ============
    banner("阶段 8 · 全链路汇总")

    async with factory() as session:
        final_episode = await EpisodeRepository(session).find_by_id(episode_id)
        final_task = await TaskRepository(session).find_by_id(task.task_id)
        stats_by_status = await EpisodeRepository(session).count_by_status()
        agent_nodes = await AgentNodeRepository(
            session, heartbeat_timeout_seconds=45
        ).find_all()
        transition_history = await TransitionRepository(session).get_history(episode_id)

    assert final_episode is not None and final_task is not None

    print(f"\n  状态轨迹（{len(status_trail)} 跳）：")
    print(f"    {' → '.join(status_trail)}")

    # 落库的流转记录。上面那条 status_trail 是 demo 自己攒的，这条是从库里读的 ——
    # 两者应当一致，不一致说明有状态变更绕过了记录点。
    print(f"\n  流转记录（{len(transition_history)} 条，从库读取）：")
    for record in transition_history:
        actor = record.actor
        who = (
            f"人工 {actor.user_id}"
            if actor.actor_type == "user"
            else f"系统 {actor.system_component}"
        )
        stamp = record.occurred_at.strftime("%H:%M:%S")
        line = f"    {stamp}  {record.from_status.value} → {record.to_status.value}  {who}"
        print(f"{line}  · {record.reason}" if record.reason else line)

    print("\n  最终 Episode：")
    print(f"    ID            {final_episode.episode_id}")
    print(f"    状态          {final_episode.status.value}"
          f"{'（终态）' if is_terminal(final_episode.status) else ''}")
    print(f"    时长          {final_episode.duration_ms}ms")
    print(f"    大小          {(final_episode.size_bytes or 0) / 1024:.1f} KiB")
    print(f"    分段          {len(final_episode.segments)}")
    print(f"    关键帧        {len(final_episode.key_frames)}")
    print(f"    质检          {'通过' if final_episode.quality and final_episode.quality.passed else '—'}")

    print("\n  分段明细：")
    for segment in sorted(final_episode.segments, key=lambda s: s.start_ms):
        source = segment.source.value if segment.source else "人工"
        print(
            f"    [{segment.start_ms:>5}ms → {segment.end_ms:>5}ms] "
            f"{segment.action_label:<8} 来源={source}"
        )

    print(f"\n  任务进度：{final_task.published_count}/{final_task.requirement.target_episode_count} "
          f"已发布 · 采集 {final_task.collected_count} 条")
    print(f"  Episode 状态分布：{stats_by_status}")
    print(f"  Agent 节点：{len(agent_nodes)} 个")

    objects = list((settings.object_store_root).rglob("*"))
    files = [p for p in objects if p.is_file()]
    print(f"\n  对象存储产物：{len(files)} 个文件")
    for path in sorted(files)[:8]:
        print(f"    {path.relative_to(settings.object_store_root)}")
    if len(files) > 8:
        print(f"    …… 另有 {len(files) - 8} 个")

    print(
        f"\n  队列状态（{queues.backend}）：待消费 ingest={await queues.depth('ingest')} "
        f"tool={await queues.depth('tool')} notify={await queues.depth('notify')}"
    )
    print(f"  死信：{await queues.dlq_count()} 条")

    await queues.close()

    success = final_episode.status is EpisodeStatus.PUBLISHED
    banner("✓ Demo 成功：Episode 走完全链路到 published" if success else "✗ Demo 未达 published")
    if success:
        print("  8 条核心交互全部走通（交互⑦ 用子进程替代 K8s Job）。")
        print(f"  运行数据保留在 {RUNTIME.relative_to(REPO_ROOT)}，可自行翻查。\n")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
