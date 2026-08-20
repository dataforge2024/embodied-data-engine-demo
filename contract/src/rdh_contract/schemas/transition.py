"""Episode 状态流转记录。

轨迹要回答两个问题：「谁在什么时候推的」与「卡在哪一步」。前者靠
:class:`TransitionActor`（人工记 user_id，系统记环节名，不把系统伪装成用户）；
后者靠相邻两条记录的时间差 —— 停留时长是可推导的，不存字段，避免不一致。

理由见 ``openspec/changes/manual-workflow-progression/design.md`` 第 7 节。
"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from ..enums import EpisodeStatus
from .base import ContractModel


class TransitionActor(ContractModel):
    """流转触发者。

    ``actor_type="user"`` 填 ``user_id``；``"system"`` 填 ``system_component``。
    两者不合并成一个字段，是因为「谁」和「哪个环节」不是同一种东西，混在一起
    读的人无法判断 ``scheduler`` 是用户名还是组件名。
    """

    actor_type: Literal["user", "system"] = Field(description="人工推进还是系统推进")
    user_id: str | None = Field(default=None, description="人工推进者；系统推进时为 None")
    system_component: str | None = Field(
        default=None,
        description="系统环节名，如 upload_callback / scheduler；人工推进时为 None",
    )


class TransitionRecord(ContractModel):
    """一条状态流转记录。

    只在状态**真的变了**时产生 —— 幂等重放（目标状态已达成）不留记录，否则轨迹里
    会出现假的停顿。
    """

    episode_id: str = Field(description="所属 Episode")
    from_status: EpisodeStatus = Field(description="源状态")
    to_status: EpisodeStatus = Field(description="目标状态")
    actor: TransitionActor = Field(description="触发者")
    reason: str | None = Field(default=None, description="打回/失败原因；正常推进为 None")
    occurred_at: datetime = Field(description="发生时间（UTC）")


__all__ = ["TransitionActor", "TransitionRecord"]
