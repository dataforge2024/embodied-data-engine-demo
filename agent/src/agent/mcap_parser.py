"""MCAP 格式嗅探与元数据解析。

两种格式：
- 标准 MCAP（二进制，magic `\\x89MCAP0\\r\\n`）→ 用 `mcap` 官方库
- 本项目 JSON Lines（首字符 `{`）→ 自行解析

两条路径产出同一个 `McapMetadata`，下游（预检、回调）不感知格式差异。
"""

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

# 标准 MCAP 文件头魔数
MCAP_MAGIC = b'\x89MCAP0\r\n'

# 本项目 JSON Lines 容器标记（与 recorder/mcap_writer.py 一致）
CONTAINER_MAGIC = 'RDHMCAP1'


class McapFormat(StrEnum):
    """支持的 MCAP 格式。"""

    STANDARD = 'standard'
    JSON_LINES = 'json_lines'


class McapMetadata(NamedTuple):
    """统一的 MCAP 元数据。"""

    format: McapFormat
    topics: tuple[str, ...]
    duration_ms: int
    message_count: int
    size_bytes: int
    checksum: str


class McapParseError(ValueError):
    """MCAP 解析失败（格式无法识别或内容残缺）。"""


def sniff_format(path: Path) -> McapFormat:
    """按文件头识别格式。

    Raises:
        McapParseError: 格式无法识别。
    """
    with path.open('rb') as f:
        head = f.read(8)

    if head.startswith(MCAP_MAGIC):
        return McapFormat.STANDARD
    if head[:1] == b'{':
        return McapFormat.JSON_LINES
    raise McapParseError(
        f'格式无法识别：文件头既非 MCAP 魔数也非 JSON（前 8 字节 {head!r}）'
    )


def parse_mcap(path: Path) -> McapMetadata:
    """解析 MCAP 文件，产出统一元数据。

    Raises:
        McapParseError: 格式无法识别或内容残缺。
    """
    if not path.is_file():
        raise McapParseError(f'文件不存在：{path}')
    if path.stat().st_size == 0:
        raise McapParseError(f'文件为空：{path}')

    fmt = sniff_format(path)
    content = path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()

    if fmt is McapFormat.STANDARD:
        topics, duration_ms, count = _parse_standard(path)
    else:
        topics, duration_ms, count = _parse_json_lines(path)

    return McapMetadata(
        format=fmt,
        topics=topics,
        duration_ms=duration_ms,
        message_count=count,
        size_bytes=len(content),
        checksum=checksum,
    )


def _parse_standard(path: Path) -> tuple[tuple[str, ...], int, int]:
    """标准 MCAP：用官方库读 topic 与时间范围。"""
    from mcap.reader import make_reader

    topics: set[str] = set()
    min_ns: int | None = None
    max_ns = 0
    count = 0

    try:
        with path.open('rb') as f:
            reader = make_reader(f)
            for _schema, channel, message in reader.iter_messages():
                topics.add(channel.topic)
                if min_ns is None or message.log_time < min_ns:
                    min_ns = message.log_time
                max_ns = max(max_ns, message.log_time)
                count += 1
    except Exception as exc:  # mcap 库对残缺文件抛多种异常类型
        raise McapParseError(f'标准 MCAP 解析失败（文件可能残缺）：{exc}') from exc

    duration_ms = int((max_ns - (min_ns or 0)) / 1_000_000)
    return tuple(sorted(topics)), duration_ms, count


def _parse_json_lines(path: Path) -> tuple[tuple[str, ...], int, int]:
    """JSON Lines：首行文件头，其后每行一条消息。

    与 `recorder/mcap_writer.py` 的写入端配对。不能 import algo_common
    （架构铁律：模块间唯一允许的依赖是 contract），因此在此重新实现。
    """
    try:
        text = path.read_text(encoding='utf-8')
    except UnicodeDecodeError as exc:
        raise McapParseError(f'文件不是有效的 UTF-8 文本：{exc}') from exc

    lines = text.strip().splitlines()
    if not lines:
        raise McapParseError('文件为空')

    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise McapParseError(f'文件头非法 JSON（文件可能残缺）：{exc}') from exc

    if header.get('magic') != CONTAINER_MAGIC:
        raise McapParseError(
            f'容器格式不匹配：期望 {CONTAINER_MAGIC}，实际 {header.get("magic")}'
        )

    topics: set[str] = set()
    max_timestamp = 0
    count = 0

    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            topics.add(raw['topic'])
            max_timestamp = max(max_timestamp, int(raw['timestamp_ms']))
            count += 1
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise McapParseError(f'第 {lineno} 行解析失败（文件可能残缺）：{exc}') from exc

    # 文件头的 duration_ms 是权威值（写入端回填），消息时间戳作兜底
    duration_ms = int(header.get('duration_ms', 0)) or max_timestamp
    return tuple(sorted(topics)), duration_ms, count


__all__ = [
    'CONTAINER_MAGIC',
    'MCAP_MAGIC',
    'McapFormat',
    'McapMetadata',
    'McapParseError',
    'parse_mcap',
    'sniff_format',
]
