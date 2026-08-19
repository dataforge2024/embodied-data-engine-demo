"""FileProcessor 主流程测试。

只测主流程 + 关键失败路径（CLAUDE.md 测试策略）：
- 解析 → 预检 → 上传 → 回调全通
- 预检不过 → .rejected/，且不上传
- 上传异常 → .failed/，且不回调
"""

import asyncio
import json
from pathlib import Path

import pytest
from rdh_contract.schemas import TaskRequirement

from agent.file_processor import FileProcessor
from agent.store.sqlite import StateStore
from agent.task_directory import TaskMetadata, write_task_metadata
from agent.uploader.chunked import UploadOutcome

TASK_ID = "11111111-2222-3333-4444-555555555555"
TOPICS = ("/camera/front", "/arm/joint_states")


def _write_mcap(path: Path, topics: tuple[str, ...] = TOPICS) -> None:
    """写一个最小可解析的 JSON Lines MCAP。"""
    lines = [
        json.dumps({"magic": "RDHMCAP1", "duration_ms": 1000}, ensure_ascii=False),
        *(
            json.dumps({"topic": t, "timestamp_ms": i * 10, "data": {"v": i}}, ensure_ascii=False)
            for i, t in enumerate(topics)
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def task_dir(tmp_path: Path) -> Path:
    """建好 .task.json 的任务目录。"""
    d = tmp_path / f"demo__{TASK_ID}"
    write_task_metadata(
        d,
        TaskMetadata(
            task_id=TASK_ID,
            task_name="demo",
            requirement=TaskRequirement(
                robot_model="rm-75-6f",
                scene="kitchen",
                required_topics=list(TOPICS),
                min_duration_ms=500,
                max_duration_ms=5000,
                target_episode_count=1,
            ),
            uploaded_count=0,
        ),
    )
    return d


class _FakePlatform:
    """记录调用的 PlatformClient 替身。"""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.started: list[str] = []
        self.completed: list[dict] = []

    async def create_episode(self, *, task_id: str, **_: object) -> str:
        episode_id = f"ep-{len(self.created)}"
        self.created.append(task_id)
        return episode_id

    async def start_upload(self, episode_id: str) -> None:
        self.started.append(episode_id)

    async def report_upload_complete(self, *, episode_id: str, **kw: object) -> None:
        self.completed.append({"episode_id": episode_id, **kw})


class _FakeUploader:
    """按需返回完整/不完整结果的上传器替身。"""

    def __init__(self, *, complete: bool = True, raises: Exception | None = None) -> None:
        self._complete = complete
        self._raises = raises
        self.calls: list[str] = []

    def upload(
        self,
        *,
        source: Path,
        object_key: str,
        already_uploaded: tuple[int, ...] = (),
        on_part_done: object = None,
    ) -> UploadOutcome:
        self.calls.append(object_key)
        if self._raises is not None:
            raise self._raises
        if callable(on_part_done):
            on_part_done(1)
        return UploadOutcome(
            object_key=object_key,
            total_parts=1,
            uploaded_parts=(1,) if self._complete else (),
            size_bytes=source.stat().st_size,
            checksum="deadbeef",
        )


def _make_processor(
    tmp_path: Path, uploader: _FakeUploader, platform: _FakePlatform
) -> FileProcessor:
    return FileProcessor(
        uploader=uploader,
        platform=platform,  # type: ignore[arg-type]
        store=StateStore(tmp_path / "state.sqlite"),
        agent_id="agent-test",
        chunk_size=256 * 1024,
    )


class TestHappyPath:
    """主流程：解析 → 预检 → 上传 → 回调 → .done/。"""

    async def test_full_flow(self, tmp_path: Path, task_dir: Path) -> None:
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert (task_dir / ".done" / "ep_001.mcap").exists()
        assert not mcap.exists()
        assert platform.created == [TASK_ID]
        assert platform.started == ["ep-0"]
        assert len(platform.completed) == 1
        assert platform.completed[0]["checksum"] == "deadbeef"

    async def test_upload_precedes_callback(self, tmp_path: Path, task_dir: Path) -> None:
        """回调必须在上传之后 —— 否则 Platform 会指向不存在的对象。"""
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert uploader.calls == ["episodes/ep-0/raw.mcap"]
        assert platform.completed[0]["object_key"] == "episodes/ep-0/raw.mcap"


class TestPrecheckRejection:
    """预检不过：不上传、不登记，移入 .rejected/ 并写原因。"""

    async def test_missing_topic_rejected(self, tmp_path: Path, task_dir: Path) -> None:
        mcap = task_dir / "ep_bad.mcap"
        _write_mcap(mcap, topics=("/camera/front",))  # 缺 /arm/joint_states
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert (task_dir / ".rejected" / "ep_bad.mcap").exists()
        error = (task_dir / ".rejected" / "ep_bad.mcap.error").read_text(encoding="utf-8")
        assert "/arm/joint_states" in error
        # 关键：不该浪费带宽，也不该在 Platform 留下孤儿 Episode
        assert uploader.calls == []
        assert platform.created == []


class TestFailurePaths:
    """上传/解析失败：移入 .failed/，不回调。"""

    async def test_upload_error_marks_failed(self, tmp_path: Path, task_dir: Path) -> None:
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader = _FakeUploader(raises=RuntimeError("OSS 连接失败"))
        platform = _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        error = (task_dir / ".failed" / "ep_001.mcap.error").read_text(encoding="utf-8")
        assert "OSS 连接失败" in error
        assert platform.completed == []

    async def test_incomplete_upload_not_callbacked(self, tmp_path: Path, task_dir: Path) -> None:
        """分片没齐不能回调 —— Platform 会以为文件完整。"""
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(complete=False), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert platform.completed == []
        assert not (task_dir / ".done" / "ep_001.mcap").exists()

    async def test_unparseable_marks_failed(self, tmp_path: Path, task_dir: Path) -> None:
        mcap = task_dir / "ep_junk.mcap"
        mcap.write_bytes(b"\x00\x01\x02not a mcap")
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert (task_dir / ".failed" / "ep_junk.mcap").exists()
        assert uploader.calls == []

    async def test_missing_metadata_leaves_file(self, tmp_path: Path) -> None:
        """没有 .task.json 时不猜任务归属，原地留着等人处理。"""
        orphan_dir = tmp_path / "no-metadata"
        orphan_dir.mkdir()
        mcap = orphan_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert mcap.exists()  # 不移动
        assert uploader.calls == []


class _FakeSocket:
    """记录进度推送的 AgentSocket 替身。"""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.progress: list[tuple[str, int, int]] = []
        self._raises = raises

    async def report_upload_progress(
        self, episode_id: str, *, uploaded_parts: int, total_parts: int
    ) -> None:
        if self._raises is not None:
            raise self._raises
        self.progress.append((episode_id, uploaded_parts, total_parts))


class TestProgressPush:
    """进度推送（tasks.md #10）。"""

    async def test_progress_pushed_during_upload(self, tmp_path: Path, task_dir: Path) -> None:
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()
        socket = _FakeSocket()

        processor = FileProcessor(
            uploader=uploader,
            platform=platform,  # type: ignore[arg-type]
            store=StateStore(tmp_path / "state.sqlite"),
            agent_id="agent-test",
            chunk_size=256 * 1024,
            socket=socket,  # type: ignore[arg-type]
        )
        await processor.process(mcap)
        await asyncio.sleep(0)  # 让 create_task 排出的推送跑完

        assert socket.progress == [("ep-0", 1, 1)]

    async def test_no_socket_skips_push(self, tmp_path: Path, task_dir: Path) -> None:
        """未接 WS 时不推进度，也不应报错。"""
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()

        await _make_processor(tmp_path, uploader, platform).process(mcap)

        assert (task_dir / ".done" / "ep_001.mcap").exists()

    async def test_push_failure_does_not_break_upload(
        self, tmp_path: Path, task_dir: Path
    ) -> None:
        """连接断开时进度推送失败，上传与回调照常完成（#10.4）。"""
        mcap = task_dir / "ep_001.mcap"
        _write_mcap(mcap)
        uploader, platform = _FakeUploader(), _FakePlatform()
        socket = _FakeSocket(raises=ConnectionError("WS 已断开"))

        processor = FileProcessor(
            uploader=uploader,
            platform=platform,  # type: ignore[arg-type]
            store=StateStore(tmp_path / "state.sqlite"),
            agent_id="agent-test",
            chunk_size=256 * 1024,
            socket=socket,  # type: ignore[arg-type]
        )
        await processor.process(mcap)
        await asyncio.sleep(0)

        assert (task_dir / ".done" / "ep_001.mcap").exists()
        assert len(platform.completed) == 1
