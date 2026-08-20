"""全局枚举。

所有跨模块的状态与分类值都在此定义，禁止在业务模块内重复声明字符串字面量。
"""

from enum import StrEnum


class EpisodeStatus(StrEnum):
    """Episode 生命周期状态。

    主链路（架构文档第二节）::

        recording → uploading → uploaded → processing
          → verification_pending → annotation_processing → annotation_pending
          → annotation_review → published

    补充的失败态（文档的核验「打回」与标注「退回」工作流隐含，但未命名）：

    - ``REJECTED``：核验打回，数据不可用，终态。
    - ``FAILED``：处理链路异常（MCAP 解析失败、算子报错），终态，需人工介入。

    标注审核「退回」不是独立状态，而是回到 ``ANNOTATION_PENDING`` 重做。

    ``ANNOTATION_PROCESSING`` 是质检通过后的送标处理环节（异步，系统推进）。它与
    ``PROCESSING`` 分开而不复用，因为两者的回调语义不同：一个是「解析完等人看」，
    一个是「送标完等人标」。理由见
    ``openspec/changes/manual-workflow-progression/design.md`` 第 1 节。
    """

    RECORDING = "recording"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    VERIFICATION_PENDING = "verification_pending"
    ANNOTATION_PROCESSING = "annotation_processing"
    ANNOTATION_PENDING = "annotation_pending"
    ANNOTATION_REVIEW = "annotation_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


class TaskStatus(StrEnum):
    """采集任务状态。"""

    DRAFT = "draft"
    PUBLISHED = "published"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Role(StrEnum):
    """RBAC 角色，对应 Platform 的工作区划分。"""

    ADMIN = "admin"
    RECORDER = "recorder"
    VERIFIER = "verifier"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"
    LAB = "lab"
    SYSOPS = "sysops"


class JobType(StrEnum):
    """Scheduler 的 4 类 worker 队列。"""

    INGEST = "ingest"
    TOOL = "tool"
    ALGO = "algo"
    NOTIFY = "notify"


class JobStatus(StrEnum):
    """Scheduler 作业 / K8s Job 状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AlgoOperator(StrEnum):
    """Algo 算子标识，与镜像名一一对应（架构文档第二节模块4）。"""

    PREANNOTATE = "preannotate"
    QUALITY = "quality"
    KEYFRAME = "keyframe"
    ANOMALY = "anomaly"


class UploadStatus(StrEnum):
    """Agent 侧分片上传状态，持久化在本地 SQLite。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewDecision(StrEnum):
    """核验与标注审核的裁决结果。"""

    APPROVE = "approve"
    REJECT = "reject"


__all__ = [
    "AlgoOperator",
    "EpisodeStatus",
    "JobStatus",
    "JobType",
    "ReviewDecision",
    "Role",
    "TaskStatus",
    "UploadStatus",
]
