"""任务目录监听与写入完成检测。

watchdog 在文件**创建时**就触发事件，此时 500MB 的 MCAP 可能只写了几 KB。
立即读取会得到残缺文件，checksum 必然不符 —— 这是目录监听最经典的失败模式。

两条检测路径：
- 大小稳定采样（默认）：每秒采样 `st_size`，连续 3 次不变视为写完。人什么都不用做。
- 完成标记文件：见到 `<name>.mcap.done` 立即处理，跳过采样。为脚本化上游预留。
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from agent.file_flow import should_ignore

logger = logging.getLogger(__name__)

DONE_MARKER_SUFFIX = '.done'


async def wait_until_stable(
    path: Path,
    *,
    interval_seconds: float = 1.0,
    stable_count: int = 3,
    timeout_seconds: float = 3600.0,
) -> bool:
    """等到文件大小稳定，或见到完成标记文件。

    Args:
        path: 目标文件。
        interval_seconds: 采样间隔。
        stable_count: 连续多少次大小不变视为写完。
        timeout_seconds: 总超时，防止无限等待。

    Returns:
        True 表示写入完成；False 表示文件消失或超时。
    """
    marker = path.with_suffix(path.suffix + DONE_MARKER_SUFFIX)
    last_size = -1
    unchanged = 0
    elapsed = 0.0

    while elapsed < timeout_seconds:
        # 标记文件优先：上游明确说写完了，不必再等采样
        if marker.exists():
            marker.unlink(missing_ok=True)
            logger.info('见到完成标记，立即处理 %s', path.name)
            return True

        if not path.exists():
            return False

        size = path.stat().st_size
        if size == last_size and size > 0:
            unchanged += 1
            if unchanged >= stable_count:
                logger.info('文件大小稳定（%d 字节）%s', size, path.name)
                return True
        else:
            unchanged = 0  # 大小变了，稳定计数重置
            last_size = size

        await asyncio.sleep(interval_seconds)
        elapsed += interval_seconds

    logger.warning('等待写入完成超时（%.0fs）%s', timeout_seconds, path.name)
    return False


class _McapEventHandler(FileSystemEventHandler):
    """把 watchdog 的同步回调桥接到 asyncio 队列。

    watchdog 在自己的线程里跑，因此用 `call_soon_threadsafe` 投递。
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[Path],
        watch_root: Path,
    ) -> None:
        self._loop = loop
        self._queue = queue
        self._watch_root = watch_root

    def on_created(self, event: FileSystemEvent) -> None:
        """文件被创建（拷入）。"""
        self._maybe_enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        """文件被移入（mv 而非 cp）。"""
        dest = getattr(event, 'dest_path', None)
        if dest:
            self._enqueue(Path(str(dest)))

    def _maybe_enqueue(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._enqueue(Path(str(event.src_path)))

    def _enqueue(self, path: Path) -> None:
        # 只处理任务目录顶层的 *.mcap，忽略阶段子目录与点号文件
        if path.suffix != '.mcap' or should_ignore(path):
            return
        if self._in_stage_dir(path):
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, path)

    def _in_stage_dir(self, path: Path) -> bool:
        """文件是否落在阶段子目录（`.uploading/` 等）里。

        只看 **相对 watch_root** 的路径段。早先这里检查绝对路径的每一段，
        而默认运行目录是 `.runtime/` —— 以点开头，于是监听根目录下的所有文件
        都被误判成阶段文件，watchdog 的实时事件全部失效。
        """
        try:
            relative = path.resolve().relative_to(self._watch_root.resolve())
        except ValueError:
            # 不在监听根目录下（软链接等），交给后续流程按普通文件处理
            return False
        return any(part.startswith('.') for part in relative.parts[:-1])


class DirectoryWatcher:
    """监听监听根目录下所有任务目录的 `*.mcap`。

    检测到写入完成后调用 `on_file_ready`。串行处理，不并发上传。
    """

    def __init__(
        self,
        watch_root: Path,
        on_file_ready: Callable[[Path], Coroutine[Any, Any, None]],
        *,
        sample_interval_seconds: float = 1.0,
        stable_sample_count: int = 3,
    ) -> None:
        self._watch_root = watch_root
        self._on_file_ready = on_file_ready
        self._interval = sample_interval_seconds
        self._stable_count = stable_sample_count
        self._queue: asyncio.Queue[Path] = asyncio.Queue()
        self._observer: Any = None

    def start(self) -> None:
        """启动 watchdog 观察者（递归监听，任务目录会动态增删）。"""
        self._watch_root.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        handler = _McapEventHandler(loop, self._queue, self._watch_root)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_root), recursive=True)
        self._observer.start()
        logger.info('目录监听已启动 %s', self._watch_root)

    def stop(self) -> None:
        """停止观察者。"""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def enqueue(self, path: Path) -> None:
        """手动入队（启动时扫描已有文件用）。"""
        self._queue.put_nowait(path)

    async def run(self) -> None:
        """处理队列，串行消费。

        每个文件先等写入完成，再交给回调。回调抛异常不中断循环 ——
        单个文件处理失败不该让整个监听停摆。
        """
        while True:
            path = await self._queue.get()
            try:
                ready = await wait_until_stable(
                    path,
                    interval_seconds=self._interval,
                    stable_count=self._stable_count,
                )
                if ready:
                    await self._on_file_ready(path)
                else:
                    logger.warning('跳过未就绪的文件 %s', path)
            except Exception:
                logger.exception('处理文件失败 %s', path)
            finally:
                self._queue.task_done()

    def scan_existing(self) -> int:
        """扫描已存在的 `*.mcap` 并入队（Agent 启动时补上监听之前落地的文件）。

        Returns:
            入队的文件数。
        """
        from agent.file_flow import list_pending_files

        count = 0
        if not self._watch_root.is_dir():
            return 0
        for task_dir in sorted(self._watch_root.iterdir()):
            if not task_dir.is_dir() or task_dir.name.startswith('.'):
                continue
            for path in list_pending_files(task_dir):
                self.enqueue(path)
                count += 1
        if count:
            logger.info('启动扫描入队 %d 个已有文件', count)
        return count


__all__ = ['DONE_MARKER_SUFFIX', 'DirectoryWatcher', 'wait_until_stable']
