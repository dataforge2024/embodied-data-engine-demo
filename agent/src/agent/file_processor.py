"""文件就绪后的处理：解析 → 预检 → 登记 → 上传 → 回调。

与 :mod:`agent.collector` 的区别：Collector 自己录制文件，本模块处理**外部落地**
的文件（由 :mod:`agent.watcher` 发现）。上传与回调的落库顺序一致 ——
每一步先写 SQLite 再执行，因此任何一步被打断都能由 :mod:`agent.recovery` 续上。
"""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent.file_flow import Stage, mark_failed, move_to_stage, precheck_topics
from agent.mcap_parser import McapMetadata, parse_mcap
from agent.platform_client import PlatformClient
from agent.progress import ProgressThrottle
from agent.store.sqlite import StateStore
from agent.task_directory import TaskMetadata, read_task_metadata
from agent.uploader.chunked import plan_parts
from agent.uploader.protocol import ChunkUploader

if TYPE_CHECKING:
    from agent.ws.client import AgentSocket

logger = logging.getLogger(__name__)


class FileProcessor:
    """处理任务目录里就绪的 `*.mcap`。"""

    def __init__(
        self,
        *,
        uploader: ChunkUploader,
        platform: PlatformClient,
        store: StateStore,
        agent_id: str,
        chunk_size: int,
        socket: 'AgentSocket | None' = None,
        throttle: ProgressThrottle | None = None,
    ) -> None:
        self._uploader = uploader
        self._platform = platform
        self._store = store
        self._agent_id = agent_id
        self._chunk_size = chunk_size
        self._socket = socket
        self._throttle = throttle if throttle is not None else ProgressThrottle()

    async def process(self, file_path: Path) -> None:
        """走完一个文件的全流程。

        不抛异常：单个文件失败不该中断监听循环（见 watcher.run）。
        失败的文件移入 `.rejected/`（预检不过）或 `.failed/`（上传/回调异常）。
        """
        task_dir = file_path.parent
        meta = read_task_metadata(task_dir)
        if meta is None:
            logger.error("缺少 .task.json，跳过 %s", file_path)
            return

        # ---- 解析 + 预检：不合格的绝不占用带宽 ----
        try:
            mcap_meta = parse_mcap(file_path)
        except Exception as exc:
            logger.warning("解析失败 %s: %s", file_path.name, exc)
            mark_failed(file_path, task_dir, stage_name="解析", error=str(exc))
            return

        result = precheck_topics(mcap_meta, meta.requirement)
        if not result.passed:
            logger.warning("预检不通过 %s：%s", file_path.name, result.reason)
            move_to_stage(file_path, task_dir, Stage.REJECTED, error_message=result.reason)
            return

        logger.info(
            "预检通过 %s（%d topic，%dms，%.1f MB）",
            file_path.name,
            len(mcap_meta.topics),
            mcap_meta.duration_ms,
            mcap_meta.size_bytes / 1024 / 1024,
        )

        staged = move_to_stage(file_path, task_dir, Stage.UPLOADING)
        try:
            await self._upload_and_report(staged, meta, mcap_meta)
        except Exception as exc:
            logger.exception("处理失败 %s", staged.name)
            mark_failed(staged, task_dir, stage_name="上传/回调", error=str(exc))
            return

        move_to_stage(staged, task_dir, Stage.DONE)

    async def _upload_and_report(
        self, staged: Path, meta: TaskMetadata, mcap_meta: McapMetadata
    ) -> None:
        """登记 → 上传 → 回调。每步先落库，异常向上抛给 process 统一收口。"""
        task_id = meta.task_id

        # ---- 登记：robot_model / scene 取自任务要求，不由 Agent 自己编 ----
        episode_id = await self._platform.create_episode(
            task_id=task_id,
            agent_id=self._agent_id,
            local_path=str(staged),
            robot_model=meta.requirement.robot_model,
            scene=meta.requirement.scene,
        )
        self._store.record_episode(episode_id=episode_id, task_id=task_id, local_path=staged)
        self._store.finish_recording(
            episode_id,
            duration_ms=mcap_meta.duration_ms,
            size_bytes=mcap_meta.size_bytes,
            checksum=mcap_meta.checksum,
            recorded_topics=mcap_meta.topics,
        )

        # ---- 上传：先建分片行，on_part_done 才有地方写 ----
        object_key = f"episodes/{episode_id}/raw.mcap"
        total_parts = plan_parts(mcap_meta.size_bytes, self._chunk_size)
        self._store.start_upload(episode_id, object_key=object_key, total_parts=total_parts)
        await self._platform.start_upload(episode_id)

        loop = asyncio.get_running_loop()

        def on_part_done(part: int) -> None:
            # uploader 在工作线程里回调，落库与推送都要切回事件循环所在线程
            loop.call_soon_threadsafe(self._on_part_done, episode_id, part, total_parts)

        # oss2 是同步 SDK，移出事件循环（protocol.py 决策）
        outcome = await asyncio.to_thread(
            self._uploader.upload,
            source=staged,
            object_key=object_key,
            on_part_done=on_part_done,
        )
        if not outcome.complete:
            # 分片没齐，留在 .uploading/ 等 recovery 续传，不标失败
            logger.warning(
                "上传未完成 %s（%d/%d 片），留待恢复",
                staged.name,
                len(outcome.uploaded_parts),
                outcome.total_parts,
            )
            raise RuntimeError(f"分片不完整：{len(outcome.uploaded_parts)}/{outcome.total_parts}")

        self._store.complete_upload(episode_id)

        # ---- 回调：checksum 用上传器实测值（移入 .uploading/ 后再算，额外完整性检查）----
        await self._platform.report_upload_complete(
            episode_id=episode_id,
            object_key=object_key,
            size_bytes=outcome.size_bytes,
            checksum=outcome.checksum,
            duration_ms=mcap_meta.duration_ms,
            recorded_topics=mcap_meta.topics,
        )
        self._store.mark_callback_done(episode_id)
        self._throttle.forget(episode_id)
        logger.info("✓ %s → episode %s（%d 片）", staged.name, episode_id, outcome.total_parts)

    def _on_part_done(self, episode_id: str, part: int, total_parts: int) -> None:
        """单片完成：落库 + 按节流推进度。在事件循环线程内执行。"""
        uploaded = self._store.mark_part_uploaded(episode_id, part)
        if self._socket is None:
            return
        if not self._throttle.should_send(
            episode_id, uploaded_parts=len(uploaded), total_parts=total_parts
        ):
            return
        # 连接断开时 report_upload_progress 会抛，这里吞掉 ——
        # 进度是可丢的，不该让它影响上传本身（tasks.md #10.4）
        task = asyncio.create_task(
            self._socket.report_upload_progress(
                episode_id, uploaded_parts=len(uploaded), total_parts=total_parts
            )
        )
        task.add_done_callback(self._swallow_progress_error)

    @staticmethod
    def _swallow_progress_error(task: 'asyncio.Task[None]') -> None:
        """进度推送失败只记日志。未取回异常会污染事件循环，因此必须消费。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("进度推送失败（忽略）：%s", exc)


__all__ = ["FileProcessor"]
