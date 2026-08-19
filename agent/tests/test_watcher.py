"""测试目录监听与写入完成检测。

主流程：文件写完被检测到 → 交给回调。
关键失败路径：仍在写入的文件不被提前处理。
"""

import asyncio
from pathlib import Path

from agent.watcher import DirectoryWatcher, wait_until_stable

# 测试用短间隔，避免每个用例等 3 秒
FAST = {'interval_seconds': 0.02, 'stable_count': 3}


class TestWaitUntilStable:
    """写入完成检测。"""

    async def test_stable_file_detected(self, tmp_path: Path):
        """大小不变的文件被判为写完。"""
        path = tmp_path / 'ep_001.mcap'
        path.write_bytes(b'x' * 1024)

        assert await wait_until_stable(path, **FAST) is True

    async def test_growing_file_waits(self, tmp_path: Path):
        """仍在增长的文件不被提前判定完成。"""
        path = tmp_path / 'ep_002.mcap'
        path.write_bytes(b'x' * 100)

        async def keep_writing() -> None:
            for _ in range(5):
                await asyncio.sleep(0.02)
                with path.open('ab') as f:
                    f.write(b'y' * 100)

        writer = asyncio.create_task(keep_writing())
        result = await wait_until_stable(path, **FAST)
        await writer

        # 写完后才返回 True，且此时文件是完整的 600 字节
        assert result is True
        assert path.stat().st_size == 600

    async def test_done_marker_skips_sampling(self, tmp_path: Path):
        """完成标记文件 → 立即处理，不等采样。"""
        path = tmp_path / 'ep_003.mcap'
        path.write_bytes(b'x' * 512)
        marker = tmp_path / 'ep_003.mcap.done'
        marker.touch()

        # 用很长的采样间隔：若走采样路径会超时，走标记路径则立即返回
        result = await wait_until_stable(path, interval_seconds=10.0, stable_count=3)

        assert result is True
        assert not marker.exists()  # 标记处理完就移除

    async def test_vanished_file(self, tmp_path: Path):
        """文件消失返回 False。"""
        assert await wait_until_stable(tmp_path / 'gone.mcap', **FAST) is False

    async def test_timeout(self, tmp_path: Path):
        """超时返回 False。"""
        path = tmp_path / 'ep_004.mcap'
        path.write_bytes(b'x')

        async def keep_growing() -> None:
            for i in range(20):
                await asyncio.sleep(0.01)
                with path.open('ab') as f:
                    f.write(bytes([i % 256]))

        writer = asyncio.create_task(keep_growing())
        result = await wait_until_stable(
            path, interval_seconds=0.01, stable_count=3, timeout_seconds=0.05
        )
        writer.cancel()

        assert result is False


class TestDirectoryWatcher:
    """watchdog 监听。"""

    async def test_detects_new_file(self, tmp_path: Path):
        """文件被拷入 → 检测到并回调。"""
        task_dir = tmp_path / 'task__t-001'
        task_dir.mkdir()

        seen: list[Path] = []
        ready = asyncio.Event()

        async def on_ready(path: Path) -> None:
            seen.append(path)
            ready.set()

        watcher = DirectoryWatcher(
            tmp_path, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.start()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.sleep(0.1)  # 等 observer 就绪
            (task_dir / 'ep_001.mcap').write_bytes(b'data' * 100)
            await asyncio.wait_for(ready.wait(), timeout=5.0)
        finally:
            runner.cancel()
            watcher.stop()

        assert len(seen) == 1
        assert seen[0].name == 'ep_001.mcap'

    async def test_ignores_non_mcap(self, tmp_path: Path):
        """非 .mcap 文件不入队。"""
        task_dir = tmp_path / 'task__t-002'
        task_dir.mkdir()

        seen: list[Path] = []

        async def on_ready(path: Path) -> None:
            seen.append(path)

        watcher = DirectoryWatcher(
            tmp_path, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.start()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.sleep(0.1)
            (task_dir / 'readme.txt').write_text('hi', encoding='utf-8')
            (task_dir / '.DS_Store').write_bytes(b'\x00')
            await asyncio.sleep(0.3)
        finally:
            runner.cancel()
            watcher.stop()

        assert seen == []

    async def test_ignores_stage_subdirs(self, tmp_path: Path):
        """阶段子目录内的文件不入队（否则 .done/ 里的会被反复处理）。"""
        task_dir = tmp_path / 'task__t-003'
        done_dir = task_dir / '.done'
        done_dir.mkdir(parents=True)

        seen: list[Path] = []

        async def on_ready(path: Path) -> None:
            seen.append(path)

        watcher = DirectoryWatcher(
            tmp_path, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.start()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.sleep(0.1)
            (done_dir / 'ep_archived.mcap').write_bytes(b'old')
            await asyncio.sleep(0.3)
        finally:
            runner.cancel()
            watcher.stop()

        assert seen == []

    async def test_scan_existing(self, tmp_path: Path):
        """启动时扫描已有文件（监听之前落地的不能漏）。"""
        for i in (1, 2):
            task_dir = tmp_path / f'task__t-00{i}'
            task_dir.mkdir()
            (task_dir / f'ep_{i}.mcap').write_bytes(b'data')
        # 阶段子目录内的不算
        (tmp_path / 'task__t-001' / '.done').mkdir()
        (tmp_path / 'task__t-001' / '.done' / 'old.mcap').write_bytes(b'x')

        async def on_ready(path: Path) -> None:
            pass

        watcher = DirectoryWatcher(tmp_path, on_ready)
        assert watcher.scan_existing() == 2

    async def test_callback_error_does_not_stop_loop(self, tmp_path: Path):
        """单个文件处理失败不中断监听。"""
        task_dir = tmp_path / 'task__t-004'
        task_dir.mkdir()
        (task_dir / 'ep_bad.mcap').write_bytes(b'data')
        (task_dir / 'ep_good.mcap').write_bytes(b'data')

        seen: list[str] = []
        second = asyncio.Event()

        async def on_ready(path: Path) -> None:
            if path.name == 'ep_bad.mcap':
                raise RuntimeError('模拟处理失败')
            seen.append(path.name)
            second.set()

        watcher = DirectoryWatcher(
            tmp_path, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.scan_existing()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.wait_for(second.wait(), timeout=5.0)
        finally:
            runner.cancel()

        # 第一个抛异常，第二个仍被处理
        assert seen == ['ep_good.mcap']

    async def test_detects_file_under_dot_prefixed_root(self, tmp_path: Path):
        """监听根目录**自身**在点号目录下时，实时事件仍要生效。

        回归测试：默认运行目录是 `.runtime/`，早先的过滤检查绝对路径的每一段，
        于是监听根目录下所有文件都被当成阶段文件丢掉，watchdog 全废。
        tmp_path 里永远没有点号段，所以原有用例发现不了。
        """
        watch_root = tmp_path / '.runtime' / 'agent' / 'tasks'
        task_dir = watch_root / 'task__t-005'
        task_dir.mkdir(parents=True)

        seen: list[Path] = []
        ready = asyncio.Event()

        async def on_ready(path: Path) -> None:
            seen.append(path)
            ready.set()

        watcher = DirectoryWatcher(
            watch_root, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.start()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.sleep(0.1)  # 等 observer 就绪
            (task_dir / 'ep_deep.mcap').write_bytes(b'data' * 100)
            await asyncio.wait_for(ready.wait(), timeout=5.0)
        finally:
            runner.cancel()
            watcher.stop()

        assert [p.name for p in seen] == ['ep_deep.mcap']

    async def test_ignores_stage_subdirs_under_dot_prefixed_root(self, tmp_path: Path):
        """点号根目录下，阶段子目录仍要被排除 —— 修复不能把闸门一起拆了。"""
        watch_root = tmp_path / '.runtime' / 'agent' / 'tasks'
        done_dir = watch_root / 'task__t-006' / '.done'
        done_dir.mkdir(parents=True)

        seen: list[Path] = []

        async def on_ready(path: Path) -> None:
            seen.append(path)

        watcher = DirectoryWatcher(
            watch_root, on_ready, sample_interval_seconds=0.02, stable_sample_count=2
        )
        watcher.start()
        runner = asyncio.create_task(watcher.run())

        try:
            await asyncio.sleep(0.1)
            (done_dir / 'ep_archived.mcap').write_bytes(b'old')
            await asyncio.sleep(0.3)
        finally:
            runner.cancel()
            watcher.stop()

        assert seen == []
