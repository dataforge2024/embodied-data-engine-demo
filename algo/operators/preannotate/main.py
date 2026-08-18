"""预标注算子：动作分段识别。

**本阶段用启发式替代模型**：真实实现是在关节状态序列上跑时序分割网络（如 TCN / Transformer）。
这里按夹爪开合状态的变化点切分动作 —— 这恰好是抓取类任务里最强的分段信号，
所以产出的分段结构与真实模型完全一致，只是精度低。

接真模型时改的只有 :meth:`PreannotateOperator.process` 内部，输出契约不变。
"""

import uuid
from typing import Any

from rdh_contract.enums import AlgoOperator

from algo_common import Operator, OperatorContext, main, read_mcap
from algo_common.io import McapFile

# 夹爪状态 topic：分段的主要信号源
GRIPPER_TOPIC = "/gripper/state"

# 关节状态 topic：无夹爪信号时的退化方案
JOINT_TOPIC = "/joint_states"

# 最短分段时长，过滤抖动产生的碎片
MIN_SEGMENT_MS = 200

# 夹爪开合的动作标签
LABEL_BY_GRIPPER = {True: "grasp", False: "move"}


class PreannotateOperator(Operator):
    """动作分段识别算子。"""

    operator = AlgoOperator.PREANNOTATE

    def process(self, context: OperatorContext) -> dict[str, Any]:
        """按夹爪状态变化点切分动作。"""
        mcap = read_mcap(context.input_path)
        segments = self._segment_by_gripper(mcap)
        if not segments:
            segments = self._segment_by_duration(mcap)
        return {"segments": segments}

    def _segment_by_gripper(self, mcap: McapFile) -> list[dict[str, Any]]:
        """按夹爪开合状态的变化点分段。"""
        messages = mcap.messages_for(GRIPPER_TOPIC)
        if not messages:
            return []

        segments: list[dict[str, Any]] = []
        current_closed = bool(messages[0].data.get("closed", False))
        start_ms = messages[0].timestamp_ms

        for message in messages[1:]:
            closed = bool(message.data.get("closed", False))
            if closed == current_closed:
                continue
            # 状态翻转：收束当前分段
            if message.timestamp_ms - start_ms >= MIN_SEGMENT_MS:
                segments.append(self._make_segment(start_ms, message.timestamp_ms, current_closed))
                start_ms = message.timestamp_ms
            current_closed = closed

        # 收尾分段延伸到 Episode 结束
        end_ms = max(mcap.duration_ms, messages[-1].timestamp_ms + MIN_SEGMENT_MS)
        if end_ms - start_ms >= MIN_SEGMENT_MS:
            segments.append(self._make_segment(start_ms, end_ms, current_closed))
        return segments

    def _segment_by_duration(self, mcap: McapFile) -> list[dict[str, Any]]:
        """无夹爪信号时的退化方案：按固定时长切分。

        真实模型不会这样做；这是为了保证流水线在任何输入下都有输出，
        便于下游人工标注在此基础上修改而非从零开始。
        """
        if mcap.duration_ms < MIN_SEGMENT_MS:
            return []
        chunk = max(mcap.duration_ms // 3, MIN_SEGMENT_MS)
        segments: list[dict[str, Any]] = []
        start = 0
        while start < mcap.duration_ms:
            end = min(start + chunk, mcap.duration_ms)
            if end - start >= MIN_SEGMENT_MS:
                segments.append(self._make_segment(start, end, False, confidence=0.35))
            start = end
        return segments

    def _make_segment(
        self, start_ms: int, end_ms: int, closed: bool, *, confidence: float = 0.72
    ) -> dict[str, Any]:
        """构造一个分段。置信度是启发式的固定值，真实模型输出 softmax 分数。"""
        label = LABEL_BY_GRIPPER[closed]
        return {
            "segment_id": str(uuid.uuid4()),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "action_label": label,
            "description": f"{'夹爪闭合' if closed else '夹爪张开'}期间的动作（预标注，待确认）",
            "source": self.operator.value,
            "confidence": confidence,
        }


if __name__ == "__main__":
    raise SystemExit(main(PreannotateOperator()))
