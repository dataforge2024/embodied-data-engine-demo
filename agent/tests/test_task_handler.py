"""测试任务推送与目录重建。"""

from datetime import UTC, datetime
from pathlib import Path

from rdh_contract.schemas import TaskRequirement
from rdh_contract.ws import TaskPushFrame

from agent.task_directory import read_task_metadata
from agent.task_handler import TaskDirectoryManager


def _requirement() -> TaskRequirement:
    return TaskRequirement(
        robot_model='rm-75-6f',
        scene='kitchen',
        required_topics=('/camera/front/image_raw', '/joint_states'),
        min_duration_ms=3000,
        max_duration_ms=30000,
        target_episode_count=5,
    )


def _push_frame(task_id: str = 't-abc123', name: str = '厨房抓取-杯子') -> TaskPushFrame:
    from rdh_contract.schemas.agent import AgentTaskPush

    return TaskPushFrame(
        message_id='msg-001',
        payload=AgentTaskPush(
            task_id=task_id,
            task_name=name,
            requirement=_requirement(),
            pushed_at=datetime.now(UTC),
        ),
    )


class TestHandleTaskPush:
    """收到任务推送 → 创建目录。"""

    def test_creates_directory_with_metadata(self, tmp_path: Path):
        """目录名可读，.task.json 内容完整。"""
        manager = TaskDirectoryManager(tmp_path)
        frame = _push_frame()

        task_dir = manager.handle_task_push(frame)

        assert task_dir.is_dir()
        assert task_dir.name == '厨房抓取-杯子__t-abc123'

        meta = read_task_metadata(task_dir)
        assert meta is not None
        assert meta.task_id == 't-abc123'
        assert meta.task_name == '厨房抓取-杯子'
        assert meta.requirement.required_topics == (
            '/camera/front/image_raw',
            '/joint_states',
        )
        assert meta.uploaded_count == 0

    def test_idempotent(self, tmp_path: Path):
        """重复推送同一任务不报错（Platform 可能重发）。"""
        manager = TaskDirectoryManager(tmp_path)
        frame = _push_frame()

        first = manager.handle_task_push(frame)
        second = manager.handle_task_push(frame)

        assert first == second
        assert len(list(tmp_path.iterdir())) == 1

    def test_special_chars_in_task_name(self, tmp_path: Path):
        """任务名含路径分隔符 → 不创建嵌套目录。"""
        manager = TaskDirectoryManager(tmp_path)
        frame = _push_frame(task_id='t-x', name='抓取/放置 (v2)')

        task_dir = manager.handle_task_push(frame)

        assert task_dir.parent == tmp_path  # 平铺，未嵌套
        assert '/' not in task_dir.name.replace('__', '')


class TestRebuildFromPlatform:
    """启动/重连时重建目录。"""

    def _task_dict(self, task_id: str, name: str, status: str = 'assigned') -> dict:
        return {
            'task_id': task_id,
            'name': name,
            'status': status,
            'requirement': _requirement().model_dump(mode='json'),
        }

    def test_rebuilds_missing_directories(self, tmp_path: Path):
        """目录被删掉后能重建。"""
        manager = TaskDirectoryManager(tmp_path)
        tasks = [
            self._task_dict('t-001', '任务一'),
            self._task_dict('t-002', '任务二'),
        ]

        manager.rebuild_from_platform(tasks)

        assert (tmp_path / '任务一__t-001' / '.task.json').is_file()
        assert (tmp_path / '任务二__t-002' / '.task.json').is_file()

    def test_skips_non_assigned(self, tmp_path: Path):
        """draft / completed 状态的任务不建目录。"""
        manager = TaskDirectoryManager(tmp_path)
        tasks = [
            self._task_dict('t-001', '草稿', status='draft'),
            self._task_dict('t-002', '已完成', status='completed'),
            self._task_dict('t-003', '进行中', status='assigned'),
        ]

        manager.rebuild_from_platform(tasks)

        assert not (tmp_path / '草稿__t-001').exists()
        assert not (tmp_path / '已完成__t-002').exists()
        assert (tmp_path / '进行中__t-003').is_dir()

    def test_preserves_existing_files(self, tmp_path: Path):
        """已有目录不被覆盖 —— 里面可能有未上传的 mcap。"""
        manager = TaskDirectoryManager(tmp_path)
        task_dir = tmp_path / '任务一__t-001'
        task_dir.mkdir()
        (task_dir / 'ep_001.mcap').write_bytes(b'recorded data')

        # 先建一次（写入 .task.json）
        manager.rebuild_from_platform([self._task_dict('t-001', '任务一')])
        # 修改 uploaded_count 模拟已上传 3 条
        from agent.task_directory import TaskMetadata, write_task_metadata

        write_task_metadata(
            task_dir, TaskMetadata('t-001', '任务一', _requirement(), uploaded_count=3)
        )

        # 再重建一次
        manager.rebuild_from_platform([self._task_dict('t-001', '任务一')])

        # 文件与计数都还在
        assert (task_dir / 'ep_001.mcap').read_bytes() == b'recorded data'
        meta = read_task_metadata(task_dir)
        assert meta is not None
        assert meta.uploaded_count == 3

    def test_empty_task_list(self, tmp_path: Path):
        """无已分派任务 → 不建任何目录。"""
        TaskDirectoryManager(tmp_path).rebuild_from_platform([])
        assert list(tmp_path.iterdir()) == []
