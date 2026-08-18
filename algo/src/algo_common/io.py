"""MCAP 读取与对象存储访问。

**算子读写对象存储的唯一收口** —— 算子不自己构造 MinIO client。
本阶段用本地文件；接 MinIO 时只改这里，4 个算子不动。

MCAP 解析本阶段用简化实现：真实 MCAP 是带 schema 的二进制容器，需 ``mcap`` 库。
demo 里 Agent 写的是「MCAP 风格」的 JSON Lines（每行一条消息），保留了
topic / timestamp / 消息体三要素，因此算子的分段与抽帧逻辑是真实可跑的。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# demo 容器格式标记，Agent 写入、算子校验
CONTAINER_MAGIC = "RDHMCAP1"


@dataclass(frozen=True)
class McapMessage:
    """一条 MCAP 消息。"""

    topic: str
    timestamp_ms: int
    data: dict[str, Any]


@dataclass(frozen=True)
class McapFile:
    """解析后的 MCAP 内容。"""

    magic: str
    episode_id: str
    duration_ms: int
    messages: tuple[McapMessage, ...]

    @property
    def topics(self) -> tuple[str, ...]:
        """出现过的 topic，按名排序。"""
        return tuple(sorted({m.topic for m in self.messages}))

    def messages_for(self, topic: str) -> tuple[McapMessage, ...]:
        """某个 topic 的消息，按时间排序。"""
        return tuple(
            sorted((m for m in self.messages if m.topic == topic), key=lambda m: m.timestamp_ms)
        )

    def camera_topics(self) -> tuple[str, ...]:
        """相机流 topic。"""
        return tuple(t for t in self.topics if "camera" in t or "image" in t)


class McapParseError(ValueError):
    """MCAP 解析失败。"""


def read_mcap(path: Path) -> McapFile:
    """解析 MCAP 文件。

    首行是文件头（magic / episode_id / duration_ms），其后每行一条消息。
    """
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if not lines:
        raise McapParseError(f"文件为空：{path}")

    try:
        header: dict[str, Any] = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise McapParseError(f"文件头非法 JSON：{exc}") from exc

    if header.get("magic") != CONTAINER_MAGIC:
        raise McapParseError(f"容器格式不匹配：期望 {CONTAINER_MAGIC}，实际 {header.get('magic')}")

    messages: list[McapMessage] = []
    for lineno, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        try:
            raw: dict[str, Any] = json.loads(line)
            messages.append(
                McapMessage(
                    topic=raw["topic"],
                    timestamp_ms=int(raw["timestamp_ms"]),
                    data=raw.get("data", {}),
                )
            )
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise McapParseError(f"第 {lineno} 行解析失败：{exc}") from exc

    return McapFile(
        magic=header["magic"],
        episode_id=str(header.get("episode_id", "")),
        duration_ms=int(header.get("duration_ms", 0)),
        messages=tuple(messages),
    )


def write_artifact(output_dir: Path, name: str, content: bytes) -> Path:
    """写一个附属产物（抽帧图片等），返回路径。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / name
    path.write_bytes(content)
    return path


__all__ = [
    "CONTAINER_MAGIC",
    "McapFile",
    "McapMessage",
    "McapParseError",
    "read_mcap",
    "write_artifact",
]
