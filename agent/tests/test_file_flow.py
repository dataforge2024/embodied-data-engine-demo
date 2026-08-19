"""测试采集要求预检与文件流转。"""

from pathlib import Path

from rdh_contract.schemas import TaskRequirement

from agent.file_flow import (
    Stage,
    list_pending_files,
    list_stage_files,
    mark_failed,
    move_to_stage,
    precheck_topics,
    reject,
    should_ignore,
)
from agent.mcap_parser import McapFormat, McapMetadata


def _requirement(*topics: str) -> TaskRequirement:
    return TaskRequirement(
        robot_model='rm-75-6f',
        scene='kitchen',
        required_topics=topics,
        min_duration_ms=3000,
        max_duration_ms=30000,
        target_episode_count=5,
    )


def _metadata(*topics: str, duration_ms: int = 8000) -> McapMetadata:
    return McapMetadata(
        format=McapFormat.JSON_LINES,
        topics=topics,
        duration_ms=duration_ms,
        message_count=100,
        size_bytes=4096,
        checksum='a' * 64,
    )


class TestPrecheck:
    """topic 比对（主流程 + 关键失败路径）。"""

    def test_all_topics_present(self):
        """全部必需 topic 存在 → 通过。"""
        result = precheck_topics(
            _metadata('/camera/front', '/joint_states'),
            _requirement('/camera/front', '/joint_states'),
        )
        assert result.passed
        assert result.missing_topics == ()

    def test_missing_topic(self):
        """缺少必需 topic → 拒绝，并列出缺哪个。"""
        result = precheck_topics(
            _metadata('/joint_states'),
            _requirement('/camera/front/image_raw', '/joint_states'),
        )
        assert not result.passed
        assert result.missing_topics == ('/camera/front/image_raw',)
        assert '/camera/front/image_raw' in result.reason

    def test_extra_topics_ok(self):
        """含要求之外的额外 topic → 不影响通过。"""
        result = precheck_topics(
            _metadata('/camera/front', '/joint_states', '/extra/debug'),
            _requirement('/camera/front'),
        )
        assert result.passed

    def test_duration_not_checked(self):
        """时长不参与校验（留给核验环节人工裁量）。"""
        # 时长远低于 min_duration_ms=3000，仍应通过
        result = precheck_topics(
            _metadata('/camera/front', duration_ms=100),
            _requirement('/camera/front'),
        )
        assert result.passed


class TestShouldIgnore:
    """静默忽略名单。"""

    def test_ignores_system_files(self, tmp_path: Path):
        assert should_ignore(tmp_path / '.DS_Store')
        assert should_ignore(tmp_path / '.task.json')

    def test_ignores_temp_suffixes(self, tmp_path: Path):
        assert should_ignore(tmp_path / 'ep_001.tmp')
        assert should_ignore(tmp_path / 'ep_001.part')

    def test_ignores_editor_temps(self, tmp_path: Path):
        assert should_ignore(tmp_path / '~$doc.mcap')

    def test_does_not_ignore_mcap(self, tmp_path: Path):
        assert not should_ignore(tmp_path / 'ep_001.mcap')


class TestFileFlow:
    """文件在阶段子目录间流转。"""

    def test_move_to_uploading(self, tmp_path: Path):
        """移入 .uploading/。"""
        source = tmp_path / 'ep_001.mcap'
        source.write_text('data', encoding='utf-8')

        target = move_to_stage(source, tmp_path, Stage.UPLOADING)

        assert not source.exists()
        assert target.exists()
        assert target.parent.name == '.uploading'
        assert target.read_text(encoding='utf-8') == 'data'

    def test_reject_writes_error_file(self, tmp_path: Path):
        """拒绝时附 .error 说明。"""
        source = tmp_path / 'ep_bad.mcap'
        source.write_text('data', encoding='utf-8')

        target = reject(source, tmp_path, '缺少必需 topic：/camera/front')

        assert target.parent.name == '.rejected'
        error_file = target.with_suffix(target.suffix + '.error')
        assert error_file.exists()
        assert '/camera/front' in error_file.read_text(encoding='utf-8')

    def test_mark_failed_records_stage_and_error(self, tmp_path: Path):
        """失败时记录阶段与错误信息。"""
        source = tmp_path / 'ep_x.mcap'
        source.write_text('data', encoding='utf-8')

        target = mark_failed(source, tmp_path, stage_name='上传', error='连接超时')

        assert target.parent.name == '.failed'
        content = target.with_suffix(target.suffix + '.error').read_text(encoding='utf-8')
        assert '上传' in content
        assert '连接超时' in content

    def test_no_silent_overwrite(self, tmp_path: Path):
        """同名文件不静默覆盖。"""
        (tmp_path / '.done').mkdir()
        (tmp_path / '.done' / 'ep_001.mcap').write_text('old', encoding='utf-8')

        source = tmp_path / 'ep_001.mcap'
        source.write_text('new', encoding='utf-8')
        target = move_to_stage(source, tmp_path, Stage.DONE)

        assert target.name == 'ep_001.1.mcap'
        assert (tmp_path / '.done' / 'ep_001.mcap').read_text(encoding='utf-8') == 'old'
        assert target.read_text(encoding='utf-8') == 'new'


class TestListFiles:
    """目录扫描忽略阶段子目录。"""

    def test_lists_only_toplevel_mcap(self, tmp_path: Path):
        """只列顶层 *.mcap。"""
        (tmp_path / 'ep_001.mcap').touch()
        (tmp_path / 'ep_002.mcap').touch()
        (tmp_path / 'readme.txt').touch()
        (tmp_path / '.task.json').touch()
        (tmp_path / '.done').mkdir()
        (tmp_path / '.done' / 'ep_old.mcap').touch()

        pending = list_pending_files(tmp_path)

        assert [p.name for p in pending] == ['ep_001.mcap', 'ep_002.mcap']

    def test_lists_stage_files(self, tmp_path: Path):
        """列某阶段子目录（断电恢复要扫 .uploading/）。"""
        (tmp_path / '.uploading').mkdir()
        (tmp_path / '.uploading' / 'ep_half.mcap').touch()

        files = list_stage_files(tmp_path, Stage.UPLOADING)

        assert [p.name for p in files] == ['ep_half.mcap']

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        """目录不存在返回空。"""
        assert list_pending_files(tmp_path / 'nope') == ()
        assert list_stage_files(tmp_path, Stage.FAILED) == ()
