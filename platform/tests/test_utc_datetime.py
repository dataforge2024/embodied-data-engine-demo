"""时间戳必须带时区标记。

回归测试：SQLite 没有原生 datetime，``DateTime(timezone=True)`` 对它是空操作，
写进去的 tz-aware 值读出来变成 naive，序列化后成了 ``2026-08-19T03:06:24``
（无偏移）。浏览器把这种字符串按本地时间解析，北京时区下整整差 8 小时。

:class:`UtcDateTime` 在边界收口，这里守住它。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UtcDateTime

pytestmark = pytest.mark.integration

BEIJING = timezone(timedelta(hours=8))


class _Stamped(Base):
    """只为本测试存在的表。"""

    __tablename__ = "_test_stamped"

    row_id: Mapped[str] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    maybe_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """内存库，只建本测试用的表。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[_Stamped.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _roundtrip(session: AsyncSession, row_id: str, value: datetime) -> datetime:
    session.add(_Stamped(row_id=row_id, at=value))
    await session.commit()
    session.expunge_all()  # 强制回库读，而不是拿身份映射里的原对象
    fetched = await session.get(_Stamped, row_id)
    assert fetched is not None
    return fetched.at


class TestUtcDateTime:
    async def test_result_is_timezone_aware(self, session: AsyncSession) -> None:
        """读回来必须带 tzinfo —— 否则序列化后没有 Z，前端会算错 8 小时。"""
        got = await _roundtrip(session, "r1", datetime(2026, 8, 19, 3, 6, 24, tzinfo=UTC))
        assert got.tzinfo is not None
        assert got.utcoffset() == timedelta(0)

    async def test_utc_instant_preserved(self, session: AsyncSession) -> None:
        """时刻本身不能被改动。"""
        original = datetime(2026, 8, 19, 3, 6, 24, 123456, tzinfo=UTC)
        assert await _roundtrip(session, "r2", original) == original

    async def test_aware_non_utc_is_converted(self, session: AsyncSession) -> None:
        """带偏移的输入换算到 UTC 存，取回是同一时刻。"""
        beijing_noon = datetime(2026, 8, 19, 12, 0, 0, tzinfo=BEIJING)
        got = await _roundtrip(session, "r3", beijing_noon)

        assert got == beijing_noon  # 同一时刻
        assert got.hour == 4  # UTC 表示
        assert got.astimezone(BEIJING).hour == 12  # 换回北京仍是中午

    async def test_naive_treated_as_utc(self, session: AsyncSession) -> None:
        """naive 输入按 UTC 解释 —— 代码里都用 now(UTC)，不该出现本地时间。"""
        got = await _roundtrip(session, "r4", datetime(2026, 8, 19, 3, 6, 24))
        assert got == datetime(2026, 8, 19, 3, 6, 24, tzinfo=UTC)

    async def test_null_passes_through(self, session: AsyncSession) -> None:
        """可空列的 None 不能被加工成时间。"""
        session.add(
            _Stamped(row_id="r5", at=datetime.now(UTC), maybe_at=None)
        )
        await session.commit()
        session.expunge_all()
        fetched = await session.get(_Stamped, "r5")
        assert fetched is not None
        assert fetched.maybe_at is None

    async def test_serializes_with_offset(self, session: AsyncSession) -> None:
        """ISO 串必须带偏移 —— 这是浏览器正确解析的前提。"""
        got = await _roundtrip(session, "r6", datetime(2026, 8, 19, 3, 6, 24, tzinfo=UTC))
        rendered = got.isoformat()
        assert rendered.endswith("+00:00"), rendered
        # 前端 new Date(...) 等价物：带偏移才能换算出北京时间 11:06
        assert datetime.fromisoformat(rendered).astimezone(BEIJING).hour == 11
