"""RabbitMQ 事件定义与注册表。"""

from .payloads import (
    AnnotationApproved,
    AnnotationProcessingRequested,
    DatasetBuildRequested,
    EpisodeRejected,
    EpisodeUploaded,
    EventEnvelope,
)
from .registry import (
    EVENT_MODELS,
    EVENT_REGISTRY,
    EXCHANGE_DLX,
    EXCHANGE_MAIN,
    EventSpec,
    UnknownEventError,
    get_model,
    get_spec,
    routing_keys_for_queue,
)

__all__ = [
    "EVENT_MODELS",
    "EVENT_REGISTRY",
    "EXCHANGE_DLX",
    "EXCHANGE_MAIN",
    "AnnotationApproved",
    "AnnotationProcessingRequested",
    "DatasetBuildRequested",
    "EpisodeRejected",
    "EpisodeUploaded",
    "EventEnvelope",
    "EventSpec",
    "UnknownEventError",
    "get_model",
    "get_spec",
    "routing_keys_for_queue",
]
