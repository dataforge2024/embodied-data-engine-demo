"""采集流程编排（交互②③）。

一条 Episode 的完整生命：登记 → 录制 → 进上传态 → 分片上传 → 回调。

**每一步先落本地库再执行**，因此任何一步被打断都能恢复（见 :mod:`agent.recovery`）。
"""

import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent.config import Settings
from agent.platform_client import PlatformClient
from agent.recorder.mcap_writer import RecordingStats, record_simulated_episode
from agent.store.sqlite import StateStore
from agent.uploader.chunked import LocalChunkUploader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectOutcome:
    """一条 Episode 的采集结果。"""

    episode_id: str
    object_key: str
    stats: RecordingStats
    total_parts: int


class Collector:
    """采集编排。"""

    def __init__(self, *, settings: Settings, store: StateStore, platform: PlatformClient) -> None:
        self._settings = settings
        self._store = store
        self._platform = platform
        self._uploader = LocalChunkUploader(
            object_store_root=settings.object_store_root,
            chunk_size=settings.chunk_size_bytes,
            max_retries=settings.max_upload_retries,
        )

    async def collect_once(
        self,
        *,
        task_id: str,
        robot_model: str,
        scene: str,
        duration_ms: int = 6000,
        inject_anomaly: bool = False,
        seed: int = 20260817,
    ) -> CollectOutcome:
        """采集一条 Episode 并走完上传与回调。"""
        # ---- 1. 登记（Platform 侧状态 recording）----
        local_path = self._settings.recording_dir / f"{uuid.uuid4()}.mcap"
        episode_id = await self._platform.create_episode(
            task_id=task_id,
            agent_id=self._settings.agent_id,
            local_path=str(local_path),
            robot_model=robot_model,
            scene=scene,
        )
        self._store.record_episode(episode_id=episode_id, task_id=task_id, local_path=local_path)
        logger.info("Episode 已登记 episode=%s 本地路径=%s", episode_id, local_path.name)

        # ---- 2. 录制 ----
        stats = record_simulated_episode(
            local_path,
            episode_id=episode_id,
            duration_ms=duration_ms,
            seed=seed,
            inject_anomaly=inject_anomaly,
        )
        self._store.finish_recording(
            episode_id,
            duration_ms=stats.duration_ms,
            size_bytes=stats.size_bytes,
            checksum=stats.checksum,
            recorded_topics=stats.topics,
        )
        logger.info(
            "录制完成 episode=%s %d 条消息 %d 字节 %d topic",
            episode_id,
            stats.message_count,
            stats.size_bytes,
            len(stats.topics),
        )

        # ---- 3. 进入上传态 ----
        await self._platform.start_upload(episode_id)

        # ---- 4. 分片上传（交互②）----
        object_key = f"episodes/{episode_id}/raw.mcap"
        outcome = self._uploader.upload(
            source=local_path,
            object_key=object_key,
            on_part_done=lambda part: self._store.mark_part_uploaded(episode_id, part),
        )
        self._store.start_upload(episode_id, object_key=object_key, total_parts=outcome.total_parts)
        for part in outcome.uploaded_parts:
            self._store.mark_part_uploaded(episode_id, part)
        self._store.complete_upload(episode_id)
        logger.info(
            "上传完成 episode=%s %d 片 checksum=%s…",
            episode_id,
            outcome.total_parts,
            outcome.checksum[:12],
        )

        # ---- 5. 上传完成回调（交互③）----
        await self._platform.report_upload_complete(
            episode_id=episode_id,
            object_key=object_key,
            size_bytes=outcome.size_bytes,
            checksum=outcome.checksum,
            duration_ms=stats.duration_ms,
            recorded_topics=stats.topics,
        )
        self._store.mark_callback_done(episode_id)
        logger.info("上传回调完成 episode=%s", episode_id)

        return CollectOutcome(
            episode_id=episode_id,
            object_key=object_key,
            stats=stats,
            total_parts=outcome.total_parts,
        )

    def disk_free_bytes(self) -> int:
        """剩余磁盘空间，心跳上报用。"""
        return shutil.disk_usage(self._settings.recording_dir).free

    def pending_upload_count(self) -> int:
        """待上传数量，心跳上报用。"""
        return self._store.pending_upload_count()


def default_recording_path(directory: Path) -> Path:
    """生成一个录制文件路径。"""
    return directory / f"{uuid.uuid4()}.mcap"


__all__ = ["CollectOutcome", "Collector", "default_recording_path"]
