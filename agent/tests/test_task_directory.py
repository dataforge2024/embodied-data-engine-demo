"""测试目录命名与元数据。"""

from pathlib import Path

from rdh_contract.schemas import TaskRequirement

from agent.task_directory import (
    TaskMetadata,
    get_task_id,
    make_directory_name,
    parse_directory_name,
    read_task_metadata,
    slugify,
    write_task_metadata,
)


class TestSlugify:
    """slugify 主流程测试（边缘 case 不测）。"""

    def test_basic(self):
        """基础字符折叠。"""
        assert slugify("厨房抓取/放置 (v2)") == "厨房抓取-放置-v2"
        assert slugify("Kitchen Grasp: Cup") == "Kitchen-Grasp-Cup"

    def test_special_chars_removed(self):
        """特殊字符去除。"""
        assert slugify("task<abc>|?*") == "taskabc"

    def test_strip_leading_trailing(self):
        """首尾 - . 去除。"""
        assert slugify("--test--") == "test"
        assert slugify("..hidden..") == "hidden"

    def test_collapse_hyphens(self):
        """连续 - 折叠。"""
        assert slugify("a---b") == "a-b"

    def test_truncate(self):
        """截断。"""
        assert len(slugify("a" * 100, max_length=60)) == 60

    def test_empty_fallback(self):
        """空串回退。"""
        assert slugify("!!!") == "task"
        assert slugify("") == "task"


class TestDirectoryName:
    """目录名构造与解析。"""

    def test_make_directory_name(self):
        """构造目录名。"""
        assert make_directory_name("厨房抓取-杯子", "t-a3f9c1") == "厨房抓取-杯子__t-a3f9c1"

    def test_parse_directory_name(self):
        """解析 task_id。"""
        assert parse_directory_name("厨房抓取-杯子__t-a3f9c1") == "t-a3f9c1"
        assert parse_directory_name("no-separator") is None

    def test_parse_with_double_underscore_in_name(self):
        """任务名含 __ 时从右侧切分。"""
        assert parse_directory_name("task__with__double__t-123") == "t-123"


class TestTaskMetadata:
    """`.task.json` 读写。"""

    def test_write_and_read(self, tmp_path: Path):
        """写入后能读回。"""
        task_dir = tmp_path / "test-task__t-001"
        req = TaskRequirement(
            robot_model="rm-75",
            scene="kitchen",
            required_topics=("/camera/front",),
            min_duration_ms=3000,
            max_duration_ms=30000,
            target_episode_count=5,
        )
        meta = TaskMetadata(
            task_id="t-001",
            task_name="测试任务",
            requirement=req,
            uploaded_count=2,
        )

        write_task_metadata(task_dir, meta)
        assert (task_dir / '.task.json').exists()

        read_meta = read_task_metadata(task_dir)
        assert read_meta is not None
        assert read_meta.task_id == "t-001"
        assert read_meta.task_name == "测试任务"
        assert read_meta.uploaded_count == 2
        assert read_meta.requirement.robot_model == "rm-75"

    def test_read_nonexistent(self, tmp_path: Path):
        """文件不存在返回 None。"""
        assert read_task_metadata(tmp_path / "nonexistent") is None

    def test_read_malformed(self, tmp_path: Path):
        """格式错误返回 None。"""
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / '.task.json').write_text("not json", encoding='utf-8')
        assert read_task_metadata(bad_dir) is None

    def test_write_overwrites(self, tmp_path: Path):
        """覆盖已存在的文件。"""
        task_dir = tmp_path / "task__t-002"
        req = TaskRequirement(
            robot_model="rm-75",
            scene="kitchen",
            required_topics=("/camera/front",),
            min_duration_ms=3000,
            max_duration_ms=30000,
            target_episode_count=5,
        )
        meta1 = TaskMetadata("t-002", "v1", req, 0)
        write_task_metadata(task_dir, meta1)

        meta2 = TaskMetadata("t-002", "v2", req, 3)
        write_task_metadata(task_dir, meta2)

        read = read_task_metadata(task_dir)
        assert read is not None
        assert read.task_name == "v2"
        assert read.uploaded_count == 3


class TestGetTaskId:
    """优先级：.task.json > 目录名。"""

    def test_from_metadata(self, tmp_path: Path):
        """优先从 .task.json 读。"""
        task_dir = tmp_path / "wrong-name__t-999"
        req = TaskRequirement(
            robot_model="rm-75",
            scene="kitchen",
            required_topics=("/camera/front",),
            min_duration_ms=3000,
            max_duration_ms=30000,
            target_episode_count=5,
        )
        write_task_metadata(
            task_dir,
            TaskMetadata("t-correct", "task", req, 0)
        )
        assert get_task_id(task_dir) == "t-correct"

    def test_fallback_to_directory_name(self, tmp_path: Path):
        """无 .task.json 时从目录名解析。"""
        task_dir = tmp_path / "task-name__t-fallback"
        task_dir.mkdir()
        assert get_task_id(task_dir) == "t-fallback"

    def test_both_fail(self, tmp_path: Path):
        """两者都失败返回 None。"""
        task_dir = tmp_path / "no-separator"
        task_dir.mkdir()
        assert get_task_id(task_dir) is None
