"""测试 MCAP 格式嗅探与解析。

主流程：两种格式产出一致的元数据结构；残缺文件报错。
"""

from pathlib import Path

import pytest

from agent.mcap_parser import (
    McapFormat,
    McapParseError,
    parse_mcap,
    sniff_format,
)
from agent.recorder.mcap_writer import record_simulated_episode


def _write_standard_mcap(path: Path, topics: dict[str, int]) -> None:
    """用 mcap 官方库写一个最小合法文件。

    Args:
        topics: topic 名 → 消息条数。
    """
    from mcap.writer import Writer

    with path.open('wb') as f:
        writer = Writer(f)
        writer.start()
        schema_id = writer.register_schema(
            name='test.Msg', encoding='jsonschema', data=b'{"type":"object"}'
        )
        for topic, count in topics.items():
            channel_id = writer.register_channel(
                topic=topic, message_encoding='json', schema_id=schema_id
            )
            for i in range(count):
                writer.add_message(
                    channel_id=channel_id,
                    log_time=i * 100_000_000,  # 100ms 间隔（纳秒）
                    data=b'{"v":1}',
                    publish_time=i * 100_000_000,
                )
        writer.finish()


class TestSniffFormat:
    """格式嗅探。"""

    def test_standard_mcap(self, tmp_path: Path):
        """标准 MCAP 魔数。"""
        path = tmp_path / 'std.mcap'
        _write_standard_mcap(path, {'/camera': 3})
        assert sniff_format(path) is McapFormat.STANDARD

    def test_json_lines(self, tmp_path: Path):
        """JSON Lines 以 { 开头。"""
        path = tmp_path / 'jl.mcap'
        record_simulated_episode(path, episode_id='ep-1', duration_ms=1000)
        assert sniff_format(path) is McapFormat.JSON_LINES

    def test_unrecognized(self, tmp_path: Path):
        """无法识别的格式报错。"""
        path = tmp_path / 'bad.mcap'
        path.write_bytes(b'PK\x03\x04garbage')
        with pytest.raises(McapParseError, match='格式无法识别'):
            sniff_format(path)


class TestParseMcap:
    """两种格式产出一致的元数据结构。"""

    def test_standard_mcap(self, tmp_path: Path):
        """标准 MCAP 解析出 topic 与时长。"""
        path = tmp_path / 'std.mcap'
        _write_standard_mcap(path, {'/camera/front': 5, '/joint_states': 5})

        meta = parse_mcap(path)
        assert meta.format is McapFormat.STANDARD
        assert meta.topics == ('/camera/front', '/joint_states')
        assert meta.duration_ms == 400  # 5 条消息，间隔 100ms，跨度 400ms
        assert meta.message_count == 10
        assert meta.size_bytes > 0
        assert len(meta.checksum) == 64

    def test_json_lines(self, tmp_path: Path):
        """JSON Lines 解析出 topic 与时长。"""
        path = tmp_path / 'jl.mcap'
        stats = record_simulated_episode(path, episode_id='ep-1', duration_ms=2000)

        meta = parse_mcap(path)
        assert meta.format is McapFormat.JSON_LINES
        assert meta.topics == stats.topics
        assert meta.duration_ms == stats.duration_ms
        assert meta.message_count == stats.message_count
        assert meta.checksum == stats.checksum

    def test_both_formats_same_structure(self, tmp_path: Path):
        """两种格式产出同样的字段集，下游不感知差异。"""
        std_path = tmp_path / 'std.mcap'
        jl_path = tmp_path / 'jl.mcap'
        _write_standard_mcap(std_path, {'/camera/front': 3})
        record_simulated_episode(jl_path, episode_id='ep-1', duration_ms=1000)

        std_meta = parse_mcap(std_path)
        jl_meta = parse_mcap(jl_path)

        assert std_meta._fields == jl_meta._fields
        for meta in (std_meta, jl_meta):
            assert isinstance(meta.topics, tuple)
            assert all(isinstance(t, str) for t in meta.topics)
            assert isinstance(meta.duration_ms, int)
            assert meta.duration_ms >= 0


class TestParseFailures:
    """残缺文件必须报错（避免上传坏数据）。"""

    def test_empty_file(self, tmp_path: Path):
        """空文件。"""
        path = tmp_path / 'empty.mcap'
        path.touch()
        with pytest.raises(McapParseError, match='文件为空'):
            parse_mcap(path)

    def test_nonexistent(self, tmp_path: Path):
        """文件不存在。"""
        with pytest.raises(McapParseError, match='文件不存在'):
            parse_mcap(tmp_path / 'nope.mcap')

    def test_truncated_standard_mcap(self, tmp_path: Path):
        """标准 MCAP 被截断 → 解析失败，不当作有效数据。"""
        path = tmp_path / 'std.mcap'
        _write_standard_mcap(path, {'/camera': 10})
        # 砍掉后半部分，模拟写入中断
        content = path.read_bytes()
        path.write_bytes(content[: len(content) // 2])

        with pytest.raises(McapParseError):
            parse_mcap(path)

    def test_truncated_json_lines(self, tmp_path: Path):
        """JSON Lines 中断在半行 → 解析失败。"""
        path = tmp_path / 'jl.mcap'
        record_simulated_episode(path, episode_id='ep-1', duration_ms=1000)
        content = path.read_text(encoding='utf-8')
        # 截在某行中间
        path.write_text(content[: len(content) - 30], encoding='utf-8')

        with pytest.raises(McapParseError, match='解析失败'):
            parse_mcap(path)

    def test_wrong_container_magic(self, tmp_path: Path):
        """JSON 但容器标记不对。"""
        path = tmp_path / 'wrong.mcap'
        path.write_text('{"magic": "OTHER", "duration_ms": 100}\n', encoding='utf-8')
        with pytest.raises(McapParseError, match='容器格式不匹配'):
            parse_mcap(path)

    def test_unrecognized_format(self, tmp_path: Path):
        """既非 MCAP 也非 JSON。"""
        path = tmp_path / 'bad.mcap'
        path.write_bytes(b'\x00\x01\x02\x03\x04\x05\x06\x07')
        with pytest.raises(McapParseError, match='格式无法识别'):
            parse_mcap(path)
