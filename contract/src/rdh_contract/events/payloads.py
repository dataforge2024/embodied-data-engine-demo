"""RabbitMQ 事件 payload（交互⑤ 发布 / 交互⑥ 消费）。

命名约定：事件名为 ``<domain>.<past-tense-verb>``，模型名为对应的驼峰形式。
所有事件都带 ``event_id`` 与 ``occurred_at``，供消费方做幂等与延迟监控。
"""

from datetime import datetime

from pydantic import Field

from ..schemas.base import ContractModel


class EventEnvelope(ContractModel):
    """事件公共字段。

    ``event_id`` 是消费方幂等去重的依据：RabbitMQ 至少一次投递，同一事件可能重复到达。
    """

    event_id: str = Field(description="事件唯一 ID（UUID），消费方据此幂等去重")
    occurred_at: datetime = Field(description="事件发生时间（UTC）")
    trace_id: str | None = Field(default=None, description="跨模块链路追踪 ID")


class EpisodeUploaded(EventEnvelope):
    """``episode.uploaded`` —— Episode 上传完成，触发处理流水线。

    发布方：Platform（收到交互③的上传回调后）。
    消费方：Scheduler ingest-worker → 解析 MCAP、抽关键帧、建索引，随后串算子。
    """

    episode_id: str = Field(description="Episode ID")
    task_id: str = Field(description="所属任务 ID")
    object_key: str = Field(description="MinIO 中的 MCAP 对象键")
    size_bytes: int = Field(ge=0, description="文件大小")
    checksum: str = Field(description="SHA-256")
    recorded_topics: tuple[str, ...] = Field(description="实际录制到的 topic")


class AnnotationApproved(EventEnvelope):
    """``annotation.approved`` —— 标注审核通过，Episode 已发布。

    发布方：Platform（审核通过、状态进 ``published`` 后）。
    消费方：Scheduler tool-worker → 格式转换、并入训练集。
    """

    episode_id: str = Field(description="Episode ID")
    task_id: str = Field(description="所属任务 ID")
    annotation_id: str = Field(description="标注记录 ID")
    segment_count: int = Field(ge=0, description="分段数量")
    approved_by: str = Field(description="审核人 user_id")


class EpisodeRejected(EventEnvelope):
    """``episode.rejected`` —— 核验打回或标注被拒，Episode 终止。

    发布方：Platform。
    消费方：notify-worker（通知采集人重采）。
    """

    episode_id: str = Field(description="Episode ID")
    task_id: str = Field(description="所属任务 ID")
    reason: str = Field(description="打回原因")
    rejected_by: str = Field(description="操作人 user_id")


class DatasetBuildRequested(EventEnvelope):
    """``dataset.build_requested`` —— Lab 工作区请求构建训练集。

    发布方：Platform（Lab 工作区）。
    消费方：Scheduler tool-worker。
    """

    dataset_id: str = Field(description="训练集 ID")
    episode_ids: tuple[str, ...] = Field(min_length=1, description="纳入的 Episode")
    output_format: str = Field(description="导出格式，如 lerobot / rlds")
    requested_by: str = Field(description="发起人 user_id")


__all__ = [
    "AnnotationApproved",
    "DatasetBuildRequested",
    "EpisodeRejected",
    "EpisodeUploaded",
    "EventEnvelope",
]
