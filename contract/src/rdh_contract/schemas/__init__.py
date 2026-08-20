"""跨模块共享数据模型（pydantic v2）。

所有模型 ``frozen=True``：变更返回新对象而非原地修改。
"""

from .agent import AgentHeartbeat, AgentNode, AgentTaskPush, UploadCallback, UploadProgress
from .annotation import Annotation, AnnotationSubmit, ReviewResult, VerifyResult
from .base import ContractModel
from .common import ApiResponse, ErrorDetail, PageMeta, PaginatedResponse
from .dataset import Dataset
from .episode import Episode, EpisodeCreate, KeyFrame, QualityReport, Segment, SensorStream
from .scheduler import (
    AlgoJobResult,
    AlgoJobRunRecord,
    AlgoJobSpec,
    AlgoResultCallback,
    AnnotationProcessingCallback,
    DatasetBuildCallback,
)
from .task import CollectTask, TaskAssignment, TaskCreate, TaskRequirement
from .transition import TransitionActor, TransitionRecord
from .user import LoginRequest, TokenPayload, TokenResponse, User

__all__ = [
    "AgentHeartbeat",
    "AgentNode",
    "AgentTaskPush",
    "AlgoJobResult",
    "AlgoJobRunRecord",
    "AlgoJobSpec",
    "AlgoResultCallback",
    "Annotation",
    "AnnotationProcessingCallback",
    "AnnotationSubmit",
    "ApiResponse",
    "CollectTask",
    "ContractModel",
    "Dataset",
    "DatasetBuildCallback",
    "Episode",
    "EpisodeCreate",
    "ErrorDetail",
    "KeyFrame",
    "LoginRequest",
    "PageMeta",
    "PaginatedResponse",
    "QualityReport",
    "ReviewResult",
    "Segment",
    "SensorStream",
    "TaskAssignment",
    "TaskCreate",
    "TaskRequirement",
    "TokenPayload",
    "TokenResponse",
    "TransitionActor",
    "TransitionRecord",
    "UploadCallback",
    "UploadProgress",
    "User",
    "VerifyResult",
]
