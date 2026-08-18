"""分片上传（交互②）。

本地用文件系统替代 MinIO，但**断点续传逻辑是真实的**：

- 每传完一片就写 SQLite，进程被杀最多重传一片
- 恢复时读 ``uploaded_parts`` 只补缺口，不从头重传
- 分片按序写入目标文件的对应偏移，与 MinIO multipart 的语义一致

接 MinIO 时改的是 :class:`LocalChunkUploader` 的 ``_write_part``（换成
``upload_part``）与 ``complete``（换成 ``complete_multipart_upload``）。
"""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadOutcome:
    """上传结果。"""

    object_key: str
    total_parts: int
    uploaded_parts: tuple[int, ...]
    size_bytes: int
    checksum: str

    @property
    def complete(self) -> bool:
        """是否全部分片就位。"""
        return len(self.uploaded_parts) == self.total_parts


def plan_parts(size_bytes: int, chunk_size: int) -> int:
    """计算分片数。空文件也算一片，避免 0 片的边界情况。"""
    if size_bytes <= 0:
        return 1
    return (size_bytes + chunk_size - 1) // chunk_size


class LocalChunkUploader:
    """本地分片上传器（MinIO 替身）。"""

    def __init__(self, *, object_store_root: Path, chunk_size: int, max_retries: int = 3) -> None:
        self._root = object_store_root
        self._chunk_size = chunk_size
        self._max_retries = max_retries

    def target_path(self, object_key: str) -> Path:
        """对象键 → 本地路径。拒绝越界键。"""
        candidate = (self._root / object_key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"非法对象键：{object_key}")
        return candidate

    def upload(
        self,
        *,
        source: Path,
        object_key: str,
        already_uploaded: tuple[int, ...] = (),
        on_part_done: Callable[[int], object] | None = None,
    ) -> UploadOutcome:
        """上传文件。

        ``already_uploaded`` 是恢复时传入的已完成分片 —— 这些片会被跳过。
        ``on_part_done`` 在每片成功后回调，调用方据此立刻落库；返回值被忽略。
        """
        size_bytes = source.stat().st_size
        total_parts = plan_parts(size_bytes, self._chunk_size)
        target = self.target_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)

        # 预分配目标文件，让分片可以按偏移乱序写入
        if not target.exists() or target.stat().st_size != size_bytes:
            with target.open("wb") as fh:
                fh.truncate(size_bytes)

        done = set(already_uploaded)
        if done:
            logger.info(
                "续传 %s：已完成 %d/%d 片，补传 %d 片",
                object_key,
                len(done),
                total_parts,
                total_parts - len(done),
            )

        with source.open("rb") as src, target.open("r+b") as dst:
            for part_number in range(1, total_parts + 1):
                if part_number in done:
                    continue
                offset = (part_number - 1) * self._chunk_size
                src.seek(offset)
                chunk = src.read(self._chunk_size)
                self._write_part(dst, offset=offset, chunk=chunk, part_number=part_number)
                done.add(part_number)
                if on_part_done is not None:
                    on_part_done(part_number)  # 立刻落库，这是续传的前提

        checksum = hashlib.sha256(target.read_bytes()).hexdigest()
        return UploadOutcome(
            object_key=object_key,
            total_parts=total_parts,
            uploaded_parts=tuple(sorted(done)),
            size_bytes=size_bytes,
            checksum=checksum,
        )

    def _write_part(self, handle: object, *, offset: int, chunk: bytes, part_number: int) -> None:
        """写单个分片，失败重试。

        真实实现在此调用 ``minio.Minio.upload_part``；重试逻辑与错误分类不变。
        """
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                handle.seek(offset)  # type: ignore[attr-defined]
                handle.write(chunk)  # type: ignore[attr-defined]
                handle.flush()  # type: ignore[attr-defined]
                return
            except OSError as exc:
                last_error = exc
                logger.warning("分片 %d 写入失败（第 %d 次）：%s", part_number, attempt, exc)
        raise OSError(f"分片 {part_number} 重试耗尽：{last_error}")


__all__ = ["LocalChunkUploader", "UploadOutcome", "plan_parts"]
