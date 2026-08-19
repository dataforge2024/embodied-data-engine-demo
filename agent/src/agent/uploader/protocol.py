"""分片上传器接口。

`ObjectStore` 在 Platform 侧是 Protocol，`LocalChunkUploader` 却是具体类 ——
这处不对称使 OSS 上传无法沿用「换实现不动调用方」的模式。本模块补上。

签名沿用现有 `LocalChunkUploader.upload()`，因此本地实现无需改动即满足。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent.uploader.chunked import UploadOutcome


@runtime_checkable
class ChunkUploader(Protocol):
    """分片上传器。

    保持同步接口：`oss2` SDK 是同步的，强行改 async 会引入线程池包装且
    不带来实际并发收益（单文件分片上传本身是串行的，续传语义要求顺序确定）。
    Agent 主循环经 `asyncio.to_thread` 把调用移出事件循环。
    """

    def upload(
        self,
        *,
        source: Path,
        object_key: str,
        already_uploaded: tuple[int, ...] = (),
        on_part_done: Callable[[int], object] | None = None,
    ) -> UploadOutcome:
        """上传文件，跳过 `already_uploaded` 中的分片。

        `on_part_done` 在每片成功后回调，调用方据此立刻落库 —— 这是续传的前提。
        """
        ...


__all__ = ['ChunkUploader']
