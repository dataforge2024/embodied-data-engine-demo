"""异常检测算子。

**本阶段用规则检测替代模型**：真实实现是在正常采集数据上训练的自编码器 / 单类分类器，
按重构误差判异常。这里检查采集过程中的物理异常信号（关节超限、力矩突变、时间戳倒退）——
这些是规则能可靠捕获的，模型主要用于捕获「说不清但不对」的情况。
"""

from typing import Any

from rdh_contract.enums import AlgoOperator

from algo_common import Operator, OperatorContext, main, read_mcap
from algo_common.io import McapFile

JOINT_TOPIC = "/joint_states"
FORCE_TOPIC = "/force_torque"

# 关节位置绝对值上限（弧度）。真实值应从机器人 URDF 读取
JOINT_LIMIT_RAD = 3.14

# 力矩突变阈值（牛米）：相邻采样跳变超过此值判为碰撞
FORCE_JUMP_THRESHOLD = 25.0


class AnomalyOperator(Operator):
    """异常检测算子。"""

    operator = AlgoOperator.ANOMALY

    def process(self, context: OperatorContext) -> dict[str, Any]:
        """检测物理异常信号。"""
        mcap = read_mcap(context.input_path)
        anomalies: list[str] = []
        anomalies.extend(self._check_timestamps(mcap))
        anomalies.extend(self._check_joint_limits(mcap))
        anomalies.extend(self._check_force_jumps(mcap))
        return {"anomalies": anomalies}

    def _check_timestamps(self, mcap: McapFile) -> list[str]:
        """时间戳必须单调递增。倒退说明录制时钟有问题，后续时序分析全不可信。"""
        issues: list[str] = []
        for topic in mcap.topics:
            messages = mcap.messages_for(topic)
            raw_order = [m.timestamp_ms for m in mcap.messages if m.topic == topic]
            if raw_order != sorted(raw_order):
                issues.append(f"{topic} 时间戳非单调递增，录制时钟异常")
            if len(messages) >= 2:
                gaps = [
                    b.timestamp_ms - a.timestamp_ms
                    for a, b in zip(messages, messages[1:], strict=False)
                ]
                # 单次间隔超过均值 10 倍，说明中间丢了一大段
                average = sum(gaps) / len(gaps)
                if average > 0 and max(gaps) > average * 10:
                    issues.append(f"{topic} 存在 {max(gaps)}ms 的数据空洞，疑似丢帧")
        return issues

    def _check_joint_limits(self, mcap: McapFile) -> list[str]:
        """关节位置超限。"""
        issues: list[str] = []
        for message in mcap.messages_for(JOINT_TOPIC):
            positions = message.data.get("positions", [])
            for index, value in enumerate(positions):
                if abs(float(value)) > JOINT_LIMIT_RAD:
                    issues.append(
                        f"关节 {index} 在 {message.timestamp_ms}ms 超限（{value:.2f} rad）"
                    )
                    break  # 每个时刻只报一次，避免刷屏
        return issues

    def _check_force_jumps(self, mcap: McapFile) -> list[str]:
        """力矩突变，通常意味着碰撞。"""
        issues: list[str] = []
        messages = mcap.messages_for(FORCE_TOPIC)
        for previous, current in zip(messages, messages[1:], strict=False):
            before = float(previous.data.get("magnitude", 0.0))
            after = float(current.data.get("magnitude", 0.0))
            if abs(after - before) > FORCE_JUMP_THRESHOLD:
                issues.append(
                    f"{current.timestamp_ms}ms 力矩突变 {before:.1f}→{after:.1f} N·m，疑似碰撞"
                )
        return issues


if __name__ == "__main__":
    raise SystemExit(main(AnomalyOperator()))
