"""算子公共库。

算子只依赖本包 + contract，不依赖 Scheduler / Platform 的任何代码。
"""

from algo_common.base import RESULT_FILENAME, Operator, OperatorContext, main
from algo_common.io import (
    CONTAINER_MAGIC,
    McapFile,
    McapMessage,
    McapParseError,
    read_mcap,
    write_artifact,
)

__all__ = [
    "CONTAINER_MAGIC",
    "RESULT_FILENAME",
    "McapFile",
    "McapMessage",
    "McapParseError",
    "Operator",
    "OperatorContext",
    "main",
    "read_mcap",
    "write_artifact",
]
