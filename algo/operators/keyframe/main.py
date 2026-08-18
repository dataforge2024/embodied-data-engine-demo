"""关键帧识别算子。

**本阶段用运动能量启发式替代模型**：真实实现是在帧序列上算特征差异或跑显著性检测。
这里取运动变化最剧烈的帧 —— 动作转折点通常就是最有信息量的帧，这个直觉与真实
关键帧算法的目标一致。

纯 CPU 算子。产出的 ``object_key`` 指向抽帧图片；本阶段写占位文件，
真实实现会解码视频帧并上传 MinIO。
"""

from typing import Any

from rdh_contract.enums import AlgoOperator

from algo_common import Operator, OperatorContext, main, read_mcap, write_artifact
from algo_common.io import McapFile

# 每个相机流最多抽多少关键帧
MAX_KEYFRAMES_PER_CAMERA = 5

# 关键帧最小间隔，避免全挤在一处
MIN_GAP_MS = 300


class KeyframeOperator(Operator):
    """关键帧识别算子。"""

    operator = AlgoOperator.KEYFRAME

    def process(self, context: OperatorContext) -> dict[str, Any]:
        """按运动能量抽取关键帧。"""
        mcap = read_mcap(context.input_path)
        key_frames: list[dict[str, Any]] = []

        for topic in mcap.camera_topics():
            key_frames.extend(self._extract_for_topic(mcap, topic, context))

        return {"key_frames": key_frames}

    def _extract_for_topic(
        self, mcap: McapFile, topic: str, context: OperatorContext
    ) -> list[dict[str, Any]]:
        """抽取单个相机流的关键帧。"""
        messages = mcap.messages_for(topic)
        if not messages:
            return []

        # 运动能量：优先用 Agent 记录的 motion，缺失时退化为均匀采样
        scored = [(m.timestamp_ms, float(m.data.get("motion", 0.0))) for m in messages]
        ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)

        selected: list[tuple[int, float]] = []
        for timestamp_ms, score in ranked:
            if len(selected) >= MAX_KEYFRAMES_PER_CAMERA:
                break
            # 与已选帧保持最小间隔，保证时间上分散
            if all(abs(timestamp_ms - t) >= MIN_GAP_MS for t, _ in selected):
                selected.append((timestamp_ms, score))

        camera_name = topic.strip("/").replace("/", "_")
        frames: list[dict[str, Any]] = []
        for timestamp_ms, score in sorted(selected):
            filename = f"{camera_name}_{timestamp_ms}.jpg"
            # 占位产物：真实实现在此解码并写入真实图像
            write_artifact(
                context.output_dir / "frames",
                filename,
                f"keyframe placeholder topic={topic} t={timestamp_ms}ms".encode(),
            )
            frames.append(
                {
                    "timestamp_ms": timestamp_ms,
                    "topic": topic,
                    "object_key": (
                        f"episodes/{context.episode_id}/algo/keyframe/frames/{filename}"
                    ),
                    "score": round(min(score, 1.0), 4),
                }
            )
        return frames


if __name__ == "__main__":
    raise SystemExit(main(KeyframeOperator()))
