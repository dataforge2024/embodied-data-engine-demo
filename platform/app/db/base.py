"""SQLAlchemy 声明式基类。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。alembic ``target_metadata`` 接这里。"""


__all__ = ["Base"]
