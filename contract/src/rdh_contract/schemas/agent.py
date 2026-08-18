"""Agent 相关模型。

覆盖交互①（WebSocket 心跳与任务推送）、交互②（分片上传进度）、交互③（上传完成回调）。
"""

from datetime import datetime

from pydantic import Field

from ..enums import UploadStatus
from .base import ContractModel
from .task import TaskRequirement


class AgentHeartbeat(ContractModel):
    """Agent 心跳（WS 上行，交互①）。

    Platform 据此判断在线状态与磁盘水位，SysOps 工作区展示。
    """

    agent_id: str = Field(description="Agent ID")
    version: str = Field(description="Agent 版本号")
    reported_at: datetime = Field(description="上报时间（UTC）")
    recording_episode_id: str | None = Field(default=None, description="正在录制的 Episode ID")
    pending_upload_count: int = Field(default=0, ge=0, description="待上传队列长度")
    disk_free_bytes: int = Field(ge=0, description="剩余磁盘空间")
    cpu_percent: float | None = Field(default=None, ge=0, le=100, description="CPU 占用")


class AgentTaskPush(ContractModel):
    """任务推送（WS 下行，交互①）。"""

    task_id: str = Field(description="任务 ID")
    task_name: str = Field(description="任务名")
    requirement: TaskRequirement = Field(description="采集要求，Agent 据此配置录制")
    pushed_at: datetime = Field(description="推送时间（UTC）")


class UploadProgress(ContractModel):
    """分片上传进度（交互②）。

    Agent 本地持久化在 SQLite，断电恢复时据 ``uploaded_parts`` 续传。
    """

    episode_id: str = Field(description="Episode ID")
    object_key: str = Field(description="MinIO 目标对象键")
    upload_id: str | None = Field(default=None, description="MinIO multipart upload ID")
    total_parts: int = Field(gt=0, description="总分片数")
    uploaded_parts: tuple[int, ...] = Field(default=(), description="已完成的分片序号（从 1 开始）")
    status: UploadStatus = Field(description="上传状态")
    last_error: str | None = Field(default=None, description="最近一次失败原因")

    @property
    def progress_ratio(self) -> float:
        """上传进度。"""
        return len(self.uploaded_parts) / self.total_parts if self.total_parts else 0.0

    @property
    def missing_parts(self) -> tuple[int, ...]:
        """待续传的分片序号。"""
        done = set(self.uploaded_parts)
        return tuple(p for p in range(1, self.total_parts + 1) if p not in done)


class UploadCallback(ContractModel):
    """上传完成回调（交互③，Agent → ``POST /callbacks/upload-complete``）。

    与交互⑧的 :class:`~rdh_contract.schemas.scheduler.AlgoResultCallback` 是**两个不同端点**，
    不要合并。本回调驱动 ``uploading → uploaded``。
    """

    episode_id: str = Field(description="Episode ID")
    object_key: str = Field(description="MinIO 对象键")
    size_bytes: int = Field(ge=0, description="文件大小")
    checksum: str = Field(description="SHA-256，Platform 侧校验完整性")
    duration_ms: int = Field(ge=0, description="采集时长")
    recorded_topics: tuple[str, ...] = Field(description="实际录制到的 topic")
    completed_at: datetime = Field(description="上传完成时间（UTC）")


class AgentNode(ContractModel):
    """Agent 节点视图（SysOps 工作区）。"""

    agent_id: str = Field(description="Agent ID")
    hostname: str = Field(description="主机名")
    version: str = Field(description="Agent 版本")
    online: bool = Field(description="是否在线（由心跳超时判定）")
    last_heartbeat: AgentHeartbeat | None = Field(default=None, description="最近一次心跳")
    assigned_task_ids: tuple[str, ...] = Field(default=(), description="已分派的任务")
    registered_at: datetime = Field(description="首次注册时间（UTC）")


__all__ = [
    "AgentHeartbeat",
    "AgentNode",
    "AgentTaskPush",
    "UploadCallback",
    "UploadProgress",
]
