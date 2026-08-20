"""训练集表。

``status`` 存 contract 的 :class:`~rdh_contract.enums.JobStatus` 值（字符串，不用数据库枚举，
与 ``episodes.status`` 同一取舍）。

``episode_ids`` 存 JSON 数组：它是一份不再变动的纳入清单，只随 dataset 整体读写，
拆关联表只会让每次查询多一次 join 而换不到任何查询能力。
"""

from datetime import datetime

from sqlalchemy import JSON, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime


class DatasetRow(Base):
    """训练集持久化行。"""

    __tablename__ = "datasets"

    dataset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    episode_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    output_format: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)

    manifest_key: Mapped[str | None] = mapped_column(String(512))
    failure_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("ix_datasets_status", "status"),)


__all__ = ["DatasetRow"]
