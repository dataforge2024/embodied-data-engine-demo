"""算子运行日志表。

回答「这一阶段自动跑了什么」——单个算子一次运行落一条，与 ``episode_transitions``
（状态流转轨迹）互补：那张表答「卡在哪个状态」，这张答「自动环节干了什么、跑了
多久、成不成功」。

不建到 ``episodes`` 的 relationship，理由与 ``TransitionRow`` 相同：查询一律按
``episode_id`` 走，async session 下的惰性加载只会带来 ``MissingGreenlet``。
"""

from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class AlgoJobRunRow(Base):
    """一条算子运行记录。只追加，不更新。"""

    __tablename__ = "algo_job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[str] = mapped_column(String(64), nullable=False)

    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)

    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        # 历史查询：按 episode 捞全部记录并按时间排序
        Index("ix_algo_job_runs_episode_time", "episode_id", "started_at"),
    )


__all__ = ["AlgoJobRunRow"]
