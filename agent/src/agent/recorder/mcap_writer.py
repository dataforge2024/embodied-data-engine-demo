"""MCAP 录制。

本阶段写「MCAP 风格」的 JSON Lines（首行文件头 + 每行一条消息），与
``algo/src/algo_common/io.py`` 的读取端配对。真实实现用 ``mcap`` 库写二进制容器 ——
换实现时改的是本模块和 io.py 两处，中间的流水线不受影响。

写入用**边录边刷盘**：录制中断时已写入的部分仍可解析，不会整条丢失。
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

CONTAINER_MAGIC = "RDHMCAP1"

# 模拟采集的 topic 与频率（Hz）
SIMULATED_TOPICS: dict[str, float] = {
    "/camera/front/image_raw": 10.0,
    "/camera/wrist/image_raw": 10.0,
    "/joint_states": 50.0,
    "/gripper/state": 20.0,
    "/force_torque": 50.0,
}

JOINT_COUNT = 6


@dataclass
class RecordingStats:
    """录制统计。"""

    duration_ms: int
    message_count: int
    size_bytes: int
    checksum: str
    topics: tuple[str, ...]


class McapWriter:
    """MCAP 写入器。

    用作上下文管理器，退出时关闭文件并保证内容落盘。
    """

    def __init__(self, path: Path, *, episode_id: str) -> None:
        self._path = path
        self._episode_id = episode_id
        self._handle: TextIO | None = None
        self._count = 0
        self._max_timestamp_ms = 0
        self._topics: set[str] = set()

    def __enter__(self) -> "McapWriter":
        """打开文件并写入文件头。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("w", encoding="utf-8")
        # duration_ms 先占位，关闭时重写整个头
        self._handle.write(
            json.dumps(
                {"magic": CONTAINER_MAGIC, "episode_id": self._episode_id, "duration_ms": 0},
                ensure_ascii=False,
            )
            + "\n"
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """关闭文件，并回填真实 duration。"""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        self._rewrite_header()

    def write_message(self, topic: str, timestamp_ms: int, data: dict[str, Any]) -> None:
        """写一条消息并立即刷盘。"""
        if self._handle is None:
            raise RuntimeError("writer 未打开")
        self._handle.write(
            json.dumps(
                {"topic": topic, "timestamp_ms": timestamp_ms, "data": data}, ensure_ascii=False
            )
            + "\n"
        )
        self._handle.flush()  # 中断时已写部分仍可解析
        self._count += 1
        self._max_timestamp_ms = max(self._max_timestamp_ms, timestamp_ms)
        self._topics.add(topic)

    def stats(self) -> RecordingStats:
        """录制统计（含 checksum，供交互③的完整性校验）。"""
        content = self._path.read_bytes()
        return RecordingStats(
            duration_ms=self._max_timestamp_ms,
            message_count=self._count,
            size_bytes=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
            topics=tuple(sorted(self._topics)),
        )

    def _rewrite_header(self) -> None:
        """回填 duration_ms —— 录制开始时还不知道总时长。"""
        if not self._path.is_file():
            return
        lines = self._path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return
        lines[0] = json.dumps(
            {
                "magic": CONTAINER_MAGIC,
                "episode_id": self._episode_id,
                "duration_ms": self._max_timestamp_ms,
            },
            ensure_ascii=False,
        )
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_simulated_episode(
    path: Path,
    *,
    episode_id: str,
    duration_ms: int = 6000,
    seed: int = 20260817,
    inject_anomaly: bool = False,
) -> RecordingStats:
    """录制一条模拟 Episode。

    生成的信号刻意包含真实采集的特征，让下游算子有东西可算：

    - 夹爪在中段闭合再张开 → 预标注算子能切出 move/grasp/move 三段
    - 相机帧带 sharpness / occlusion / motion 元数据 → 质检与关键帧算子有输入
    - ``inject_anomaly=True`` 时注入力矩突变 → 异常检测算子能报出来

    真实实现是订阅 ROS topic 并写入；本函数是它的替身。
    """
    rng = random.Random(seed)

    # 抓取动作的时间安排：前 1/3 接近，中 1/3 抓取，后 1/3 搬运
    grasp_start = duration_ms // 3
    grasp_end = duration_ms * 2 // 3

    with McapWriter(path, episode_id=episode_id) as writer:
        for topic, frequency in SIMULATED_TOPICS.items():
            interval_ms = max(int(1000 / frequency), 1)
            for timestamp_ms in range(0, duration_ms, interval_ms):
                writer.write_message(
                    topic,
                    timestamp_ms,
                    _make_payload(
                        topic,
                        timestamp_ms,
                        duration_ms=duration_ms,
                        grasp_start=grasp_start,
                        grasp_end=grasp_end,
                        rng=rng,
                        inject_anomaly=inject_anomaly,
                    ),
                )
        stats = writer.stats()
    # 文件头回填后 checksum 变了，重新计算
    content = path.read_bytes()
    return RecordingStats(
        duration_ms=stats.duration_ms,
        message_count=stats.message_count,
        size_bytes=len(content),
        checksum=hashlib.sha256(content).hexdigest(),
        topics=stats.topics,
    )


def _make_payload(
    topic: str,
    timestamp_ms: int,
    *,
    duration_ms: int,
    grasp_start: int,
    grasp_end: int,
    rng: random.Random,
    inject_anomaly: bool,
) -> dict[str, Any]:
    """构造一条消息的负载。"""
    progress = timestamp_ms / duration_ms if duration_ms else 0.0

    if "camera" in topic:
        # 抓取瞬间运动最剧烈 → 关键帧算子会挑中这些帧
        distance_to_grasp = abs(timestamp_ms - (grasp_start + grasp_end) / 2)
        motion = max(0.05, 1.0 - distance_to_grasp / (duration_ms / 2))
        return {
            "frame_id": timestamp_ms // 100,
            "sharpness": round(0.82 + rng.uniform(-0.05, 0.05), 4),
            "occlusion": round(0.12 + rng.uniform(0, 0.08), 4),
            "motion": round(motion, 4),
        }

    if topic == "/joint_states":
        # 平滑的正弦轨迹，幅度在关节限位内
        return {
            "positions": [
                round(1.2 * math.sin(2 * math.pi * progress + index * 0.4), 4)
                for index in range(JOINT_COUNT)
            ],
            "velocities": [
                round(0.3 * math.cos(2 * math.pi * progress + index * 0.4), 4)
                for index in range(JOINT_COUNT)
            ],
        }

    if topic == "/gripper/state":
        closed = grasp_start <= timestamp_ms < grasp_end
        return {"closed": closed, "width_mm": 2.0 if closed else 65.0}

    if topic == "/force_torque":
        base = 12.0 if grasp_start <= timestamp_ms < grasp_end else 2.0
        magnitude = base + rng.uniform(-0.5, 0.5)
        # 注入一次碰撞级突变，让异常检测算子有东西可报
        if inject_anomaly and abs(timestamp_ms - grasp_start) < 20:
            magnitude += 60.0
        return {"magnitude": round(magnitude, 4)}

    return {}


__all__ = [
    "CONTAINER_MAGIC",
    "JOINT_COUNT",
    "SIMULATED_TOPICS",
    "McapWriter",
    "RecordingStats",
    "record_simulated_episode",
]
