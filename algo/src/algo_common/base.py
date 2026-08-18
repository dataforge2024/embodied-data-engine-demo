"""算子基类。

算子的**执行环境契约**（由 Scheduler 通过环境变量注入，见 ``scheduler/k8s/job_builder.py``）：

===================== ====================================================
环境变量               含义
===================== ====================================================
``RDH_JOB_ID``         作业 ID
``RDH_EPISODE_ID``     待处理 Episode
``RDH_OPERATOR``       算子类型
``RDH_INPUT_PATH``     输入 MCAP 的本地路径（生产为 MinIO 对象键）
``RDH_OUTPUT_DIR``     产物输出目录（生产为 MinIO 前缀）
``RDH_MODEL_VERSION``  模型版本（镜像 tag）
===================== ====================================================

算子**只负责产出业务数据**，``job_id`` / ``status`` / 时间戳等编排字段由 Scheduler 补齐。
算子不碰 K8s API、不直连数据库、不调 Platform —— 它是一个纯粹的「读输入、算、写输出」进程。
"""

import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rdh_contract.enums import AlgoOperator

logger = logging.getLogger(__name__)

RESULT_FILENAME = "result.json"


@dataclass(frozen=True)
class OperatorContext:
    """算子运行上下文，从环境变量构造。"""

    job_id: str
    episode_id: str
    operator: AlgoOperator
    input_path: Path
    output_dir: Path
    model_version: str

    @classmethod
    def from_env(cls) -> "OperatorContext":
        """从环境变量构造。缺失必要变量直接失败 —— 不猜默认值。"""
        required = (
            "RDH_JOB_ID",
            "RDH_EPISODE_ID",
            "RDH_OPERATOR",
            "RDH_INPUT_PATH",
            "RDH_OUTPUT_DIR",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"缺少环境变量：{', '.join(missing)}")

        return cls(
            job_id=os.environ["RDH_JOB_ID"],
            episode_id=os.environ["RDH_EPISODE_ID"],
            operator=AlgoOperator(os.environ["RDH_OPERATOR"]),
            input_path=Path(os.environ["RDH_INPUT_PATH"]),
            output_dir=Path(os.environ["RDH_OUTPUT_DIR"]),
            model_version=os.environ.get("RDH_MODEL_VERSION", "unknown"),
        )


class Operator(ABC):
    """算子基类。

    子类只需实现 :meth:`process`，返回符合
    :class:`~rdh_contract.schemas.scheduler.AlgoJobResult` 部分字段的字典。
    """

    #: 本算子对应的类型
    operator: AlgoOperator

    @abstractmethod
    def process(self, context: OperatorContext) -> dict[str, Any]:
        """执行推理，返回业务字段。

        返回值只含本算子负责的字段（如 ``segments`` / ``quality``），
        编排字段由 Scheduler 补齐。
        """

    def run(self, context: OperatorContext) -> Path:
        """执行并把结果写到 ``RDH_OUTPUT_DIR/result.json``。

        产物用「临时文件 + 原子 rename」落盘，避免 Scheduler 读到写一半的 JSON。
        """
        if not context.input_path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{context.input_path}")

        logger.info(
            "算子开始 operator=%s episode=%s input=%s",
            self.operator.value,
            context.episode_id,
            context.input_path.name,
        )
        payload = self.process(context)

        context.output_dir.mkdir(parents=True, exist_ok=True)
        final_path = context.output_dir / RESULT_FILENAME
        tmp_path = context.output_dir / f".{RESULT_FILENAME}.tmp"
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.rename(final_path)

        logger.info("算子完成 operator=%s 产物=%s", self.operator.value, final_path)
        return final_path


def main(operator: Operator) -> int:
    """算子入口的统一实现。

    异常转成非零退出码 + stderr 信息，Scheduler 据此判定失败并记录原因。
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    try:
        context = OperatorContext.from_env()
        operator.run(context)
    except Exception as exc:
        print(f"算子执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


__all__ = ["RESULT_FILENAME", "Operator", "OperatorContext", "main"]
