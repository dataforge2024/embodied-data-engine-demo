"""阿里云 OSS 分片上传。

满足 `ChunkUploader` Protocol，调用方（文件流转逻辑）不感知是本地还是 OSS。

凭据从环境变量读取，Agent 直接上传 —— 不依赖 Platform 签发临时凭据，
避免 HTTP 与 WS 两条通道的时序耦合。见 design.md 决策 6。

**OSS 须配置「清理未完成分片」生命周期规则**：阿里云对未完成的分片上传
持续计存储费用，失败的上传会静默积累成本。此项无法由代码解决。
"""

import hashlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.uploader.chunked import UploadOutcome, plan_parts

logger = logging.getLogger(__name__)

# OSS 分片下限 100KB（最后一片除外），低于此值 OSS 会拒绝
OSS_MIN_PART_SIZE = 100 * 1024


@dataclass(frozen=True)
class OSSConfig:
    """OSS 连接配置。"""

    access_key_id: str
    access_key_secret: str
    endpoint: str
    bucket: str

    @classmethod
    def from_env(cls) -> 'OSSConfig':
        """从环境变量读取，缺失则报错。

        Agent 启动时调用。宁可起不来，也不要静默降级到本地存储。

        Raises:
            RuntimeError: 任一变量缺失或为空。
        """
        keys = ('OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'OSS_ENDPOINT', 'OSS_BUCKET')
        values = {k: os.environ.get(k, '').strip() for k in keys}
        missing = [k for k, v in values.items() if not v]
        if missing:
            raise RuntimeError(
                f'OSS 配置缺失：{", ".join(missing)}。'
                f'经 .env.oss 注入（见 .env.oss.example），'
                f'或设 RDH_OBJECT_STORE_BACKEND=local 用本地替身。'
            )
        return cls(
            access_key_id=values['OSS_ACCESS_KEY_ID'],
            access_key_secret=values['OSS_ACCESS_KEY_SECRET'],
            endpoint=values['OSS_ENDPOINT'],
            bucket=values['OSS_BUCKET'],
        )


class OSSChunkUploader:
    """OSS 分片上传器，满足 `ChunkUploader` Protocol。

    断点续传：`already_uploaded` 里的分片不重传。OSS 的 multipart upload
    需要 upload_id 才能续传，因此 upload_id 必须由调用方持久化并回传。
    """

    def __init__(
        self,
        config: OSSConfig,
        *,
        chunk_size: int = 1024 * 1024,
        max_retries: int = 3,
        bucket_client: Any = None,
    ) -> None:
        """
        Args:
            bucket_client: 注入的 bucket 客户端，仅测试用。生产传 None 走真实 SDK。
        """
        self._config = config
        # OSS 对非末片有 100KB 下限，低于此值直接抬到下限
        self._chunk_size = max(chunk_size, OSS_MIN_PART_SIZE)
        self._max_retries = max_retries
        self._bucket = bucket_client if bucket_client is not None else self._make_bucket()

    def _make_bucket(self) -> Any:
        """构造 oss2 bucket 客户端。"""
        import oss2  # type: ignore[import-untyped]

        auth = oss2.Auth(self._config.access_key_id, self._config.access_key_secret)
        return oss2.Bucket(auth, self._config.endpoint, self._config.bucket)

    def upload(
        self,
        *,
        source: Path,
        object_key: str,
        already_uploaded: tuple[int, ...] = (),
        on_part_done: Callable[[int], object] | None = None,
        upload_id: str | None = None,
    ) -> UploadOutcome:
        """分片上传到 OSS。

        Args:
            upload_id: 续传时传入已有的 OSS upload_id；None 则新建。
        """
        size_bytes = source.stat().st_size
        total_parts = plan_parts(size_bytes, self._chunk_size)

        if upload_id is None:
            upload_id = self._bucket.init_multipart_upload(object_key).upload_id
            logger.info('OSS 分片上传开始 %s upload_id=%s', object_key, upload_id)

        done = set(already_uploaded)
        if done:
            logger.info(
                '续传 %s：已完成 %d/%d 片，补传 %d 片',
                object_key,
                len(done),
                total_parts,
                total_parts - len(done),
            )

        # 续传时需要已完成分片的 ETag 才能 complete，从 OSS 侧列取
        parts = self._list_existing_parts(object_key, upload_id) if done else []

        with source.open('rb') as fh:
            for part_number in range(1, total_parts + 1):
                if part_number in done:
                    continue
                offset = (part_number - 1) * self._chunk_size
                fh.seek(offset)
                chunk = fh.read(self._chunk_size)
                etag = self._upload_part(object_key, upload_id, part_number, chunk)
                parts.append(self._make_part_info(part_number, etag))
                done.add(part_number)
                if on_part_done is not None:
                    on_part_done(part_number)  # 立刻落库，这是续传的前提

        parts.sort(key=lambda p: p.part_number)
        self._bucket.complete_multipart_upload(object_key, upload_id, parts)
        logger.info('OSS 分片上传完成 %s（%d 片）', object_key, total_parts)

        # checksum 由本地文件算 —— 与回调中声明的必须一致，Platform 会独立重算比对
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        return UploadOutcome(
            object_key=object_key,
            total_parts=total_parts,
            uploaded_parts=tuple(sorted(done)),
            size_bytes=size_bytes,
            checksum=checksum,
        )

    def _upload_part(self, object_key: str, upload_id: str, part_number: int, chunk: bytes) -> str:
        """上传单片，失败重试。返回 ETag。"""
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                result = self._bucket.upload_part(object_key, upload_id, part_number, chunk)
                return str(result.etag)
            except Exception as exc:  # oss2 抛多种异常类型
                last_error = exc
                logger.warning('分片 %d 上传失败（第 %d 次）：%s', part_number, attempt, exc)
        raise RuntimeError(f'分片 {part_number} 重试耗尽：{last_error}')

    def _list_existing_parts(self, object_key: str, upload_id: str) -> list[Any]:
        """列出 OSS 侧已完成的分片，续传时 complete 需要它们的 ETag。"""
        import oss2

        parts: list[Any] = []
        for part in oss2.PartIterator(self._bucket, object_key, upload_id):
            parts.append(oss2.models.PartInfo(part.part_number, part.etag, size=part.size))
        return parts

    def _make_part_info(self, part_number: int, etag: str) -> Any:
        """构造 PartInfo。测试注入 mock 客户端时不依赖 oss2。"""
        try:
            import oss2

            return oss2.models.PartInfo(part_number, etag)
        except ImportError:  # pragma: no cover
            from types import SimpleNamespace

            return SimpleNamespace(part_number=part_number, etag=etag)


__all__ = ['OSS_MIN_PART_SIZE', 'OSSChunkUploader', 'OSSConfig']
