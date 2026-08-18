"""数据库引擎与会话。"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.base import Base


@lru_cache
def get_engine() -> AsyncEngine:
    """进程级引擎单例。"""
    settings = get_settings()
    return create_async_engine(settings.database_url, echo=False, future=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """会话工厂。``expire_on_commit=False`` 让提交后仍可读取对象属性。"""
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话，异常时回滚。"""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_schema() -> None:
    """建表。

    demo 用 ``create_all``；生产走 alembic 迁移（``alembic/`` 已就位）。
    """
    # 导入模型以注册到 metadata
    from app import models  # noqa: F401

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["get_engine", "get_session", "get_session_factory", "init_schema"]
