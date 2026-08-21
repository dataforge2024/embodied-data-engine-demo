"""训练集导出。

``POST /datasets/build`` 受理后异步构建，:class:`Dataset` 是 ``GET /datasets/{id}``
的返回视图 —— 没有它，构建完成与否只能翻日志。

构建状态复用 :class:`~rdh_contract.enums.JobStatus`，不新增枚举：导出本身就是一个
Scheduler 作业，``pending``（已受理待处理）→ ``running``（构建中）→
``succeeded`` / ``failed`` 正好覆盖「进行中 / 完成 / 失败」三态。
"""

from datetime import datetime

from pydantic import Field

from ..enums import JobStatus
from .base import ContractModel


class Dataset(ContractModel):
    """训练集构建视图。

    本阶段产物是 ``manifest.json`` 清单而非可训练的打包数据，理由见
    ``openspec/changes/archive/2026-08-21-manual-workflow-progression/design.md`` 第 5 节。
    """

    dataset_id: str = Field(description="训练集 ID（UUID）")
    status: JobStatus = Field(description="构建状态")

    episode_ids: tuple[str, ...] = Field(
        min_length=1, description="纳入清单；构建时校验必须全部为 published"
    )
    output_format: str = Field(description="导出格式，如 lerobot / rlds")
    requested_by: str = Field(description="发起人 user_id")

    manifest_key: str | None = Field(
        default=None, description="产物位置（manifest.json 的对象键）；未构建完成时为 None"
    )
    failure_reason: str | None = Field(default=None, description="构建失败原因")

    created_at: datetime = Field(description="受理时间（UTC）")
    updated_at: datetime = Field(description="最后更新时间（UTC）")


__all__ = ["Dataset"]
