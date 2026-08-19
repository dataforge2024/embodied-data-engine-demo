"""Episode 及其派生结构。

Episode 是一次采集的最小单元，对应一个 MCAP 文件。

``Segment`` 是**跨三个模块共享**的关键结构：Algo 预标注算子输出它，Tool 展示与编辑它，
Platform 存储它。改动此模型影响面最大。
"""

from datetime import datetime

from pydantic import Field, model_validator

from ..enums import AlgoOperator, EpisodeStatus
from .base import ContractModel


class SensorStream(ContractModel):
    """MCAP 内的一路传感器流。

    多视角回放依赖 ``topic`` 区分相机，依赖 ``start_offset_ms`` 做多路对齐。
    """

    topic: str = Field(description="MCAP topic 名，如 /camera/front/image_raw")
    kind: str = Field(description="流类型：camera / joint_state / tactile / audio")
    message_count: int = Field(ge=0, description="消息条数")
    frequency_hz: float | None = Field(default=None, gt=0, description="采样频率")
    start_offset_ms: int = Field(
        default=0, ge=0, description="相对 Episode 起点的偏移，多路同步回放用"
    )
    preview_url: str | None = Field(default=None, description="转码后的预览视频地址（相机流）")


class KeyFrame(ContractModel):
    """关键帧。由 ingest-worker 抽取或 keyframe 算子识别。"""

    timestamp_ms: int = Field(ge=0, description="相对 Episode 起点的毫秒时间戳")
    topic: str = Field(description="来源 topic")
    object_key: str = Field(description="MinIO 对象键（抽帧图片）")
    score: float | None = Field(default=None, ge=0, le=1, description="关键帧显著性得分")


class Segment(ContractModel):
    """动作分段。

    生产方：``preannotate`` 算子（``source=AlgoOperator.PREANNOTATE``），
    或人工标注（``source=None``）。
    消费方：Tool 时间轴编辑器、Platform 训练集构建。
    """

    segment_id: str = Field(description="分段 ID（UUID）")
    start_ms: int = Field(ge=0, description="起始毫秒偏移")
    end_ms: int = Field(gt=0, description="结束毫秒偏移，必须大于 start_ms")
    action_label: str | None = Field(default=None, description="动作标签，如 grasp / place")
    description: str | None = Field(default=None, max_length=2000, description="自然语言动作描述")
    source: AlgoOperator | None = Field(
        default=None, description="算子来源；None 表示人工创建或人工修改"
    )
    confidence: float | None = Field(default=None, ge=0, le=1, description="算子置信度")

    @model_validator(mode="after")
    def _check_range(self) -> "Segment":
        """时间区间必须非空。"""
        if self.end_ms <= self.start_ms:
            raise ValueError(
                f"分段区间非法：end_ms({self.end_ms}) 必须大于 start_ms({self.start_ms})"
            )
        return self

    @property
    def duration_ms(self) -> int:
        """分段时长。"""
        return self.end_ms - self.start_ms


class QualityReport(ContractModel):
    """质检算子输出。"""

    passed: bool = Field(description="是否通过质检")
    blur_score: float | None = Field(default=None, ge=0, le=1, description="模糊程度，越大越模糊")
    occlusion_score: float | None = Field(default=None, ge=0, le=1, description="遮挡程度")
    issues: tuple[str, ...] = Field(default=(), description="问题清单")


class EpisodeCreate(ContractModel):
    """创建 Episode（Agent 开始录制时上报）。"""

    task_id: str = Field(description="所属采集任务 ID")
    agent_id: str = Field(description="采集 PC 的 Agent ID")
    recorded_by: str | None = Field(
        default=None, description="采集员 user_id；Agent 无人值守采集时为 None"
    )
    local_path: str = Field(description="Agent 本地 MCAP 路径，用于断电恢复定位")
    robot_model: str | None = Field(default=None, description="机器人型号")
    scene: str | None = Field(default=None, description="采集场景标识")


class Episode(ContractModel):
    """Episode 完整视图。

    ``status`` 的所有变更必须经 Platform ``services/episode_lifecycle.py``，
    由 :func:`rdh_contract.state_machine.assert_transition` 守卫。
    """

    episode_id: str = Field(description="Episode ID（UUID）")
    task_id: str = Field(description="所属采集任务 ID")
    agent_id: str = Field(description="采集来源 Agent ID")
    recorded_by: str | None = Field(
        default=None, description="采集员 user_id；Agent 无人值守采集时为 None"
    )
    status: EpisodeStatus = Field(description="当前状态")

    object_key: str | None = Field(default=None, description="MinIO 中的 MCAP 对象键")
    size_bytes: int | None = Field(default=None, ge=0, description="MCAP 文件大小")
    duration_ms: int | None = Field(default=None, ge=0, description="采集时长")
    checksum: str | None = Field(default=None, description="MCAP 的 SHA-256，上传完整性校验")

    streams: tuple[SensorStream, ...] = Field(default=(), description="传感器流索引，ingest 产出")
    key_frames: tuple[KeyFrame, ...] = Field(default=(), description="关键帧")
    segments: tuple[Segment, ...] = Field(default=(), description="动作分段")
    quality: QualityReport | None = Field(default=None, description="质检结果")

    robot_model: str | None = Field(default=None, description="机器人型号")
    scene: str | None = Field(default=None, description="采集场景标识")
    reject_reason: str | None = Field(default=None, description="打回或失败原因")

    created_at: datetime = Field(description="创建时间（UTC）")
    updated_at: datetime = Field(description="最后更新时间（UTC）")


__all__ = [
    "Episode",
    "EpisodeCreate",
    "KeyFrame",
    "QualityReport",
    "Segment",
    "SensorStream",
]
