"""Agent ↔ Platform WebSocket 协议（交互①）。

帧格式统一为 ``{"type": <MessageType>, "payload": {...}}``，用 ``type`` 判别式解析。
上行（Agent → Platform）与下行（Platform → Agent）分成两个联合类型，方向不可混用。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from ..enums import EpisodeStatus
from ..schemas.agent import AgentHeartbeat, AgentTaskPush
from ..schemas.base import ContractModel

WS_PROTOCOL_VERSION = "1.0"
"""协议版本。Agent 连接时在握手参数中带上，Platform 拒绝不兼容的主版本。"""

HEARTBEAT_INTERVAL_SECONDS = 15
"""Agent 心跳间隔。"""

HEARTBEAT_TIMEOUT_SECONDS = 45
"""Platform 判定离线的超时阈值（3 个心跳周期）。"""


class MessageType(StrEnum):
    """WS 消息类型。前缀标明方向：``up`` 为 Agent 发出，``down`` 为 Platform 发出。"""

    # 上行
    UP_REGISTER = "up.register"
    UP_HEARTBEAT = "up.heartbeat"
    UP_EPISODE_STATUS = "up.episode_status"
    UP_UPLOAD_PROGRESS = "up.upload_progress"
    UP_ACK = "up.ack"

    # 下行
    DOWN_REGISTERED = "down.registered"
    DOWN_TASK_PUSH = "down.task_push"
    DOWN_TASK_CANCEL = "down.task_cancel"
    DOWN_UPLOAD_GRANT = "down.upload_grant"
    DOWN_ERROR = "down.error"


# ---- 上行消息 ----


class RegisterFrame(ContractModel):
    """Agent 注册（连接建立后的第一帧）。"""

    type: Literal[MessageType.UP_REGISTER] = MessageType.UP_REGISTER
    agent_id: str = Field(description="Agent ID")
    hostname: str = Field(description="主机名")
    version: str = Field(description="Agent 版本")
    protocol_version: str = Field(description="WS 协议版本")


class HeartbeatFrame(ContractModel):
    """心跳。"""

    type: Literal[MessageType.UP_HEARTBEAT] = MessageType.UP_HEARTBEAT
    payload: AgentHeartbeat = Field(description="心跳内容")


class EpisodeStatusFrame(ContractModel):
    """Agent 侧 Episode 状态变化上报（如录制开始/结束）。

    Platform 收到后仍需经 ``episode_lifecycle`` 守卫，Agent 的上报不是权威决定。
    """

    type: Literal[MessageType.UP_EPISODE_STATUS] = MessageType.UP_EPISODE_STATUS
    episode_id: str = Field(description="Episode ID")
    status: EpisodeStatus = Field(description="Agent 观察到的状态")
    reported_at: datetime = Field(description="上报时间（UTC）")
    detail: str | None = Field(default=None, description="补充说明，如中断原因")


class UploadProgressFrame(ContractModel):
    """上传进度上报（交互②的进度，供 SysOps 观察）。"""

    type: Literal[MessageType.UP_UPLOAD_PROGRESS] = MessageType.UP_UPLOAD_PROGRESS
    episode_id: str = Field(description="Episode ID")
    uploaded_parts: int = Field(ge=0, description="已完成分片数")
    total_parts: int = Field(gt=0, description="总分片数")


class AckFrame(ContractModel):
    """确认收到下行消息。"""

    type: Literal[MessageType.UP_ACK] = MessageType.UP_ACK
    message_id: str = Field(description="被确认的下行消息 ID")


# ---- 下行消息 ----


class RegisteredFrame(ContractModel):
    """注册成功。"""

    type: Literal[MessageType.DOWN_REGISTERED] = MessageType.DOWN_REGISTERED
    message_id: str = Field(description="消息 ID")
    session_id: str = Field(description="本次连接的会话 ID")
    heartbeat_interval_seconds: int = Field(gt=0, description="要求的心跳间隔")


class TaskPushFrame(ContractModel):
    """任务推送。"""

    type: Literal[MessageType.DOWN_TASK_PUSH] = MessageType.DOWN_TASK_PUSH
    message_id: str = Field(description="消息 ID，Agent 需回 ack")
    payload: AgentTaskPush = Field(description="任务内容")


class TaskCancelFrame(ContractModel):
    """任务取消。"""

    type: Literal[MessageType.DOWN_TASK_CANCEL] = MessageType.DOWN_TASK_CANCEL
    message_id: str = Field(description="消息 ID")
    task_id: str = Field(description="被取消的任务 ID")
    reason: str | None = Field(default=None, description="取消原因")


class UploadGrantFrame(ContractModel):
    """下发上传凭据（交互②前置）。

    Platform 签发 MinIO 预签名地址，Agent 不持有长期对象存储凭据。
    """

    type: Literal[MessageType.DOWN_UPLOAD_GRANT] = MessageType.DOWN_UPLOAD_GRANT
    message_id: str = Field(description="消息 ID")
    episode_id: str = Field(description="Episode ID")
    object_key: str = Field(description="目标对象键")
    presigned_url: str = Field(description="预签名上传地址")
    expires_at: datetime = Field(description="凭据过期时间（UTC）")


class ErrorFrame(ContractModel):
    """下行错误。"""

    type: Literal[MessageType.DOWN_ERROR] = MessageType.DOWN_ERROR
    message_id: str = Field(description="消息 ID")
    code: str = Field(description="错误码")
    message: str = Field(description="错误描述，不含内部细节")
    fatal: bool = Field(default=False, description="是否需要 Agent 断开重连")


UpstreamFrame = Annotated[
    RegisterFrame | HeartbeatFrame | EpisodeStatusFrame | UploadProgressFrame | AckFrame,
    Field(discriminator="type"),
]
"""Agent → Platform 的消息联合类型。"""

DownstreamFrame = Annotated[
    RegisteredFrame | TaskPushFrame | TaskCancelFrame | UploadGrantFrame | ErrorFrame,
    Field(discriminator="type"),
]
"""Platform → Agent 的消息联合类型。"""

UPSTREAM_ADAPTER: TypeAdapter[UpstreamFrame] = TypeAdapter(UpstreamFrame)
"""解析上行帧。Platform 侧用：``UPSTREAM_ADAPTER.validate_json(raw)``。"""

DOWNSTREAM_ADAPTER: TypeAdapter[DownstreamFrame] = TypeAdapter(DownstreamFrame)
"""解析下行帧。Agent 侧用：``DOWNSTREAM_ADAPTER.validate_json(raw)``。"""


__all__ = [
    "DOWNSTREAM_ADAPTER",
    "HEARTBEAT_INTERVAL_SECONDS",
    "HEARTBEAT_TIMEOUT_SECONDS",
    "UPSTREAM_ADAPTER",
    "WS_PROTOCOL_VERSION",
    "AckFrame",
    "DownstreamFrame",
    "EpisodeStatusFrame",
    "ErrorFrame",
    "HeartbeatFrame",
    "MessageType",
    "RegisterFrame",
    "RegisteredFrame",
    "TaskCancelFrame",
    "TaskPushFrame",
    "UploadGrantFrame",
    "UploadProgressFrame",
    "UpstreamFrame",
]
