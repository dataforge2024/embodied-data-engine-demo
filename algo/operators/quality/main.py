"""质检算子：模糊 / 遮挡检测。

**本阶段用元数据启发式替代 CV 模型**：真实实现是对抽帧图像跑拉普拉斯方差（模糊）
与分割模型（遮挡）。这里用 Agent 写入的帧元数据（亮度、清晰度评分）做判断 ——
判定逻辑与阈值结构是真实的，只是特征来源不同。

质检是纯 CPU 算子（`GPU_REQUIREMENTS` 里声明为 0），因为轻量 CV 不需要 GPU。
"""

from statistics import mean
from typing import Any

from rdh_contract.enums import AlgoOperator

from algo_common import Operator, OperatorContext, main, read_mcap
from algo_common.io import McapFile

# 清晰度低于此值判为模糊。真实实现用拉普拉斯方差，阈值需按相机标定
BLUR_THRESHOLD = 0.35

# 遮挡比例高于此值判为不合格
OCCLUSION_THRESHOLD = 0.40

# 相机流最少帧数，太少说明录制中断
MIN_FRAMES_PER_CAMERA = 5


class QualityOperator(Operator):
    """质检算子。"""

    operator = AlgoOperator.QUALITY

    def process(self, context: OperatorContext) -> dict[str, Any]:
        """检测模糊与遮挡，产出质检报告。"""
        mcap = read_mcap(context.input_path)
        issues: list[str] = []

        blur_score = self._blur_score(mcap)
        occlusion_score = self._occlusion_score(mcap)

        if blur_score is not None and blur_score > BLUR_THRESHOLD:
            issues.append(f"画面模糊（模糊度 {blur_score:.2f} > {BLUR_THRESHOLD}）")
        if occlusion_score is not None and occlusion_score > OCCLUSION_THRESHOLD:
            issues.append(f"目标遮挡严重（遮挡度 {occlusion_score:.2f} > {OCCLUSION_THRESHOLD}）")

        cameras = mcap.camera_topics()
        if not cameras:
            issues.append("未录制到任何相机流")
        for topic in cameras:
            frame_count = len(mcap.messages_for(topic))
            if frame_count < MIN_FRAMES_PER_CAMERA:
                issues.append(f"{topic} 仅 {frame_count} 帧，疑似录制中断")

        return {
            "quality": {
                "passed": not issues,
                "blur_score": blur_score,
                "occlusion_score": occlusion_score,
                "issues": issues,
            }
        }

    def _blur_score(self, mcap: McapFile) -> float | None:
        """模糊度：清晰度的补数。无相机帧时返回 None。"""
        scores = [
            float(m.data["sharpness"])
            for topic in mcap.camera_topics()
            for m in mcap.messages_for(topic)
            if "sharpness" in m.data
        ]
        return round(1.0 - mean(scores), 4) if scores else None

    def _occlusion_score(self, mcap: McapFile) -> float | None:
        """遮挡度：各帧遮挡比例均值。"""
        scores = [
            float(m.data["occlusion"])
            for topic in mcap.camera_topics()
            for m in mcap.messages_for(topic)
            if "occlusion" in m.data
        ]
        return round(mean(scores), 4) if scores else None


if __name__ == "__main__":
    raise SystemExit(main(QualityOperator()))
