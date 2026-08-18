"""事件注册表 —— routing_key 与 payload 模型的唯一映射。

Platform 的 ``event_publisher`` 按此表校验并发布；Scheduler 的 ``consumers/rabbit`` 按此表
把 routing_key 路由到对应 Celery 队列。双方都不得硬编码 routing_key 字符串。
"""

from collections.abc import Mapping

from ..enums import JobType
from ..schemas.base import ContractModel
from .payloads import (
    AlgoCompleted,
    AlgoFailed,
    AnnotationApproved,
    DatasetBuildRequested,
    EpisodeRejected,
    EpisodeUploaded,
)

# 主 exchange：topic 类型，按 routing_key 分发
EXCHANGE_MAIN = "robotdatahub.events"

# 死信 exchange：重试耗尽的消息进此处，由人工或补偿任务处理
EXCHANGE_DLX = "robotdatahub.dlx"


class EventSpec(ContractModel):
    """一条事件的完整规格。"""

    routing_key: str
    """RabbitMQ routing key，格式 ``<domain>.<past-tense-verb>``。"""

    model_name: str
    """payload 模型类名，与 :data:`EVENT_MODELS` 的取值对应。"""

    exchange: str
    """所属 exchange。"""

    consumer_queue: JobType
    """消费方队列（Scheduler 的 4 类 worker 之一）。"""

    description: str
    """事件语义说明。"""

    max_retries: int = 3
    """重试上限，耗尽后进死信队列。"""


# routing_key → payload 模型类。生成 JSON Schema 与消费方反序列化都用它。
EVENT_MODELS: Mapping[str, type[ContractModel]] = {
    "episode.uploaded": EpisodeUploaded,
    "episode.rejected": EpisodeRejected,
    "algo.completed": AlgoCompleted,
    "algo.failed": AlgoFailed,
    "annotation.approved": AnnotationApproved,
    "dataset.build_requested": DatasetBuildRequested,
}

# routing_key → 事件规格
EVENT_REGISTRY: Mapping[str, EventSpec] = {
    "episode.uploaded": EventSpec(
        routing_key="episode.uploaded",
        model_name="EpisodeUploaded",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.INGEST,
        description="Episode 上传完成，触发解析与算子流水线",
    ),
    "episode.rejected": EventSpec(
        routing_key="episode.rejected",
        model_name="EpisodeRejected",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.NOTIFY,
        description="Episode 被打回，通知采集人重采",
    ),
    "algo.completed": EventSpec(
        routing_key="algo.completed",
        model_name="AlgoCompleted",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.NOTIFY,
        description="单个算子执行成功，聚合后回调 Platform",
    ),
    "algo.failed": EventSpec(
        routing_key="algo.failed",
        model_name="AlgoFailed",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.NOTIFY,
        description="算子执行失败，回调 Platform 置 failed 并告警",
        max_retries=1,
    ),
    "annotation.approved": EventSpec(
        routing_key="annotation.approved",
        model_name="AnnotationApproved",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.TOOL,
        description="标注审核通过，触发格式转换与训练集并入",
    ),
    "dataset.build_requested": EventSpec(
        routing_key="dataset.build_requested",
        model_name="DatasetBuildRequested",
        exchange=EXCHANGE_MAIN,
        consumer_queue=JobType.TOOL,
        description="Lab 请求构建训练集",
    ),
}


class UnknownEventError(KeyError):
    """未注册的 routing_key。"""

    def __init__(self, routing_key: str) -> None:
        self.routing_key = routing_key
        known = ", ".join(sorted(EVENT_REGISTRY))
        super().__init__(f"未注册的事件 routing_key：{routing_key}；已注册：{known}")


def get_spec(routing_key: str) -> EventSpec:
    """按 routing_key 取事件规格，未注册则抛 :class:`UnknownEventError`。"""
    try:
        return EVENT_REGISTRY[routing_key]
    except KeyError:
        raise UnknownEventError(routing_key) from None


def get_model(routing_key: str) -> type[ContractModel]:
    """按 routing_key 取 payload 模型，未注册则抛 :class:`UnknownEventError`。"""
    try:
        return EVENT_MODELS[routing_key]
    except KeyError:
        raise UnknownEventError(routing_key) from None


def routing_keys_for_queue(queue: JobType) -> tuple[str, ...]:
    """列出某个 worker 队列需要订阅的 routing_key。"""
    return tuple(
        sorted(key for key, spec in EVENT_REGISTRY.items() if spec.consumer_queue is queue)
    )


__all__ = [
    "EVENT_MODELS",
    "EVENT_REGISTRY",
    "EXCHANGE_DLX",
    "EXCHANGE_MAIN",
    "EventSpec",
    "UnknownEventError",
    "get_model",
    "get_spec",
    "routing_keys_for_queue",
]
