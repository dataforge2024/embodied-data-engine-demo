"""采集任务。

Admin 工作区创建任务并下发给 Recorder；Platform 经 WebSocket 推送给 Agent（交互①）。
"""

from datetime import datetime

from pydantic import Field, model_validator

from ..enums import TaskStatus
from .base import ContractModel


class TaskRequirement(ContractModel):
    """采集要求。Agent 据此配置录制，Tool 核验时据此判断是否达标。"""

    robot_model: str = Field(description="指定机器人型号")
    scene: str = Field(description="采集场景")
    required_topics: tuple[str, ...] = Field(description="必须录制的 topic，缺失则核验不通过")
    min_duration_ms: int = Field(ge=0, description="单条 Episode 最短时长")
    max_duration_ms: int = Field(gt=0, description="单条 Episode 最长时长")
    target_episode_count: int = Field(gt=0, description="目标采集条数")

    @model_validator(mode="after")
    def _check_duration(self) -> "TaskRequirement":
        """时长上限必须大于下限。"""
        if self.max_duration_ms <= self.min_duration_ms:
            raise ValueError(
                f"时长区间非法：max_duration_ms({self.max_duration_ms}) "
                f"必须大于 min_duration_ms({self.min_duration_ms})"
            )
        return self


class TaskCreate(ContractModel):
    """创建采集任务（Admin）。"""

    name: str = Field(min_length=1, max_length=200, description="任务名")
    description: str | None = Field(default=None, max_length=5000, description="任务说明")
    requirement: TaskRequirement = Field(description="采集要求")


class TaskAssignment(ContractModel):
    """任务分派记录：把任务指派给某个 Agent 节点。"""

    task_id: str = Field(description="任务 ID")
    agent_id: str = Field(description="被指派的 Agent ID")
    assigned_by: str = Field(description="操作人 user_id")
    assigned_at: datetime = Field(description="指派时间（UTC）")


class CollectTask(ContractModel):
    """采集任务完整视图。"""

    task_id: str = Field(description="任务 ID（UUID）")
    name: str = Field(description="任务名")
    description: str | None = Field(default=None, description="任务说明")
    status: TaskStatus = Field(description="任务状态")
    requirement: TaskRequirement = Field(description="采集要求")

    collected_count: int = Field(default=0, ge=0, description="已采集条数")
    published_count: int = Field(default=0, ge=0, description="已发布条数")
    assignments: tuple[TaskAssignment, ...] = Field(default=(), description="分派记录")

    created_by: str = Field(description="创建人 user_id")
    created_at: datetime = Field(description="创建时间（UTC）")
    updated_at: datetime = Field(description="最后更新时间（UTC）")

    @property
    def progress_ratio(self) -> float:
        """采集进度（已发布 / 目标）。"""
        target = self.requirement.target_episode_count
        return min(self.published_count / target, 1.0) if target else 0.0


__all__ = ["CollectTask", "TaskAssignment", "TaskCreate", "TaskRequirement"]
