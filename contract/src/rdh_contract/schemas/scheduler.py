"""Scheduler 相关模型。

覆盖交互⑦（创建 K8s Job 运行算子）与交互⑧（结果回调 Platform）。
"""

from datetime import datetime

from pydantic import Field, model_validator

from ..enums import AlgoOperator, JobStatus
from .base import ContractModel
from .episode import KeyFrame, QualityReport, Segment


class AlgoJobSpec(ContractModel):
    """算子作业参数（Scheduler → K8s Job，交互⑦）。

    Scheduler 用它构造 Job manifest 并以环境变量注入 Pod；算子从环境读取。
    Pod 完成后由 TTL 自动清理（``ttl_seconds``）。
    """

    job_id: str = Field(description="作业 ID（UUID），同时作为 K8s Job 名后缀")
    episode_id: str = Field(description="待处理的 Episode ID")
    operator: AlgoOperator = Field(description="算子类型")
    image: str = Field(description="算子镜像，含 tag —— tag 即模型版本")
    input_object_key: str = Field(description="输入 MCAP 的 MinIO 对象键")
    output_prefix: str = Field(description="输出产物的 MinIO 前缀")
    gpu_count: int = Field(default=1, ge=0, le=8, description="GPU 数量，0 表示纯 CPU 算子")
    timeout_seconds: int = Field(default=3600, gt=0, description="超时时间")
    ttl_seconds: int = Field(default=300, ge=0, description="Job 完成后的自动清理延迟")


class AlgoJobResult(ContractModel):
    """算子输出（Algo → MinIO，Scheduler 读取）。

    四类算子的产出是互斥的，由 ``operator`` 决定哪个字段有值：

    - ``PREANNOTATE`` → ``segments``
    - ``QUALITY`` → ``quality``
    - ``KEYFRAME`` → ``key_frames``
    - ``ANOMALY`` → ``anomalies``
    """

    job_id: str = Field(description="作业 ID")
    episode_id: str = Field(description="Episode ID")
    operator: AlgoOperator = Field(description="算子类型")
    status: JobStatus = Field(description="作业最终状态")
    model_version: str = Field(description="模型版本（镜像 tag）")

    segments: tuple[Segment, ...] = Field(default=(), description="预标注分段")
    key_frames: tuple[KeyFrame, ...] = Field(default=(), description="关键帧")
    quality: QualityReport | None = Field(default=None, description="质检报告")
    anomalies: tuple[str, ...] = Field(default=(), description="异常描述")

    error_message: str | None = Field(default=None, description="失败原因")
    started_at: datetime = Field(description="开始时间（UTC）")
    finished_at: datetime = Field(description="结束时间（UTC）")

    @model_validator(mode="after")
    def _require_error_on_failure(self) -> "AlgoJobResult":
        """失败与超时必须给出原因，否则排障无从下手。"""
        if self.status in (JobStatus.FAILED, JobStatus.TIMEOUT) and not self.error_message:
            raise ValueError(f"status={self.status.value} 必须填写 error_message")
        return self

    @property
    def duration_seconds(self) -> float:
        """执行耗时。"""
        return (self.finished_at - self.started_at).total_seconds()


class AlgoResultCallback(ContractModel):
    """算子结果回调（交互⑧，Scheduler → ``POST /callbacks/algo-result``）。

    与交互③的 :class:`~rdh_contract.schemas.agent.UploadCallback` 是**两个不同端点**。
    本回调驱动 ``processing → verification_pending``（全部算子成功）或 ``processing → failed``。

    ``pipeline_complete`` 用于区分「单个算子完成」与「整条流水线完成」：
    只有后者才触发状态流转，前者仅落数据。
    """

    episode_id: str = Field(description="Episode ID")
    results: tuple[AlgoJobResult, ...] = Field(min_length=1, description="本批算子结果")
    pipeline_complete: bool = Field(
        description="整条流水线是否已完成；仅为 True 时 Platform 才推进 Episode 状态"
    )
    reported_at: datetime = Field(description="回调时间（UTC）")

    @property
    def all_succeeded(self) -> bool:
        """是否全部算子成功。"""
        return all(r.status is JobStatus.SUCCEEDED for r in self.results)


__all__ = ["AlgoJobResult", "AlgoJobSpec", "AlgoResultCallback"]
