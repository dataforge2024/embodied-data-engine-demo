"""断电恢复。

Agent 启动时扫本地状态库，把未完成的工作接着做完。两类残局：

1. **上传没传完** —— 从 ``uploaded_parts`` 续传缺口，不重传已完成分片
2. **传完了但回调没成功** —— 补发交互③的回调

第 2 类最容易被忽略：文件已经在对象存储里，但 Platform 不知道，Episode 永远卡在
``uploading``。恢复时必须把这种情况也捞出来。
"""

import logging
from dataclasses import dataclass

from agent.config import Settings
from agent.platform_client import PlatformClient, PlatformError
from agent.store.sqlite import EpisodeRecord, StateStore
from agent.uploader.chunked import LocalChunkUploader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryReport:
    """恢复结果。"""

    scanned: int
    resumed_uploads: int
    resent_callbacks: int
    failed: tuple[str, ...]

    @property
    def had_work(self) -> bool:
        """是否确实有残局需要处理。"""
        return self.resumed_uploads > 0 or self.resent_callbacks > 0


class RecoveryService:
    """启动恢复。"""

    def __init__(self, *, settings: Settings, store: StateStore, platform: PlatformClient) -> None:
        self._settings = settings
        self._store = store
        self._platform = platform
        self._uploader = LocalChunkUploader(
            object_store_root=settings.object_store_root,
            chunk_size=settings.chunk_size_bytes,
            max_retries=settings.max_upload_retries,
        )

    async def recover(self) -> RecoveryReport:
        """扫描并处理全部残局。"""
        unfinished = self._store.unfinished()
        if not unfinished:
            logger.info("无未完成 Episode，无需恢复")
            return RecoveryReport(scanned=0, resumed_uploads=0, resent_callbacks=0, failed=())

        logger.info("发现 %d 个未完成 Episode，开始恢复", len(unfinished))
        resumed = 0
        resent = 0
        failed: list[str] = []

        for record in unfinished:
            try:
                if record.needs_upload:
                    await self._resume_upload(record)
                    resumed += 1
                    # 续传后重新读取，拿到最新的 checksum 等信息
                    refreshed = self._store.get(record.episode_id)
                    if refreshed is not None and refreshed.needs_callback:
                        await self._resend_callback(refreshed)
                        resent += 1
                elif record.needs_callback:
                    await self._resend_callback(record)
                    resent += 1
            except (OSError, PlatformError, ValueError) as exc:
                logger.error("恢复失败 episode=%s: %s", record.episode_id, exc)
                self._store.fail_upload(record.episode_id, error=str(exc))
                failed.append(record.episode_id)

        return RecoveryReport(
            scanned=len(unfinished),
            resumed_uploads=resumed,
            resent_callbacks=resent,
            failed=tuple(failed),
        )

    async def _resume_upload(self, record: EpisodeRecord) -> None:
        """续传一个 Episode。"""
        if not record.local_path.is_file():
            raise OSError(f"本地文件已丢失：{record.local_path}")

        object_key = record.object_key or f"episodes/{record.episode_id}/raw.mcap"
        logger.info(
            "续传 episode=%s 已完成 %d/%d 片",
            record.episode_id,
            len(record.uploaded_parts),
            record.total_parts,
        )

        outcome = self._uploader.upload(
            source=record.local_path,
            object_key=object_key,
            already_uploaded=record.uploaded_parts,
            on_part_done=lambda part: self._store.mark_part_uploaded(record.episode_id, part),
        )
        self._store.start_upload(
            record.episode_id, object_key=object_key, total_parts=outcome.total_parts
        )
        for part in outcome.uploaded_parts:
            self._store.mark_part_uploaded(record.episode_id, part)
        self._store.complete_upload(record.episode_id)

    async def _resend_callback(self, record: EpisodeRecord) -> None:
        """补发上传完成回调。"""
        if record.object_key is None or record.checksum is None:
            raise ValueError(f"缺少回调所需元信息：episode={record.episode_id}")

        logger.info("补发上传回调 episode=%s", record.episode_id)
        await self._platform.report_upload_complete(
            episode_id=record.episode_id,
            object_key=record.object_key,
            size_bytes=record.size_bytes or 0,
            checksum=record.checksum,
            duration_ms=record.duration_ms or 0,
            recorded_topics=record.recorded_topics,
        )
        self._store.mark_callback_done(record.episode_id)


__all__ = ["RecoveryReport", "RecoveryService"]
