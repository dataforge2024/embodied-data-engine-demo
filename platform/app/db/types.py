"""自定义列类型。

SQLite 没有原生 datetime，``DateTime(timezone=True)`` 对它是空操作：写进去的
tz-aware 值读出来变成 naive，序列化后就成了 ``2026-08-19T03:06:24`` —— 没有偏移
标记。浏览器把这种字符串按**本地**时间解析，于是北京时区下整整差 8 小时。

在边界上收口：写入统一归一到 UTC，读出统一补回 UTC。库里始终是 UTC，
时区转换只发生在展示层。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """始终以 UTC 存取的 datetime。

    - 绑定参数：naive 值视为 UTC，aware 值换算到 UTC
    - 取回结果：给 naive 值补上 UTC tzinfo，让下游序列化带出 ``+00:00``
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            # naive 一律当 UTC —— 代码里都用 datetime.now(UTC)，不该出现本地时间
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


__all__ = ["UtcDateTime"]
