"""上传进度节流落库（tasks.md #11.1）。

Agent 每传完一片就推一帧，500MB / 256KB = 2000 帧。每帧一次 UPDATE 会把
进度写放大成主要的 DB 负载，而看板并不需要这个精度。

节流条件（满足其一即写）：
- 距上次落库 ≥ 2 秒
- 进度增量 ≥ 5%
- 末片（uploaded == total）—— 100% 必须准确落库，否则看板永远停在 95%

节流状态按连接持有（见 handlers.py），Agent 断开即丢弃，不必跨连接保留。
"""

import logging
import time
from typing import NamedTuple

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 2.0
MIN_PERCENT_DELTA = 5.0


class _LastWrite(NamedTuple):
    """上次落库的时间与百分比。"""

    at: float
    percent: float


class ProgressThrottle:
    """决定某条进度是否值得落库。只做决策，不碰 DB —— 便于单测。"""

    def __init__(
        self,
        *,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        min_percent_delta: float = MIN_PERCENT_DELTA,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._min_delta = min_percent_delta
        self._last: dict[str, _LastWrite] = {}

    def should_write(
        self,
        episode_id: str,
        *,
        uploaded_parts: int,
        total_parts: int,
        now: float | None = None,
    ) -> bool:
        """是否该落库。

        Args:
            episode_id: Episode ID，节流按它分组。
            uploaded_parts: 已完成分片数。
            total_parts: 总分片数（帧里 gt=0，此处不再防 0）。
            now: 当前单调时钟，测试注入用。
        """
        current = time.monotonic() if now is None else now
        percent = uploaded_parts / total_parts * 100

        # 末片无条件落库
        if uploaded_parts >= total_parts:
            self._last[episode_id] = _LastWrite(current, percent)
            return True

        last = self._last.get(episode_id)
        if last is None:
            self._last[episode_id] = _LastWrite(current, percent)
            return True

        if current - last.at >= self._min_interval or percent - last.percent >= self._min_delta:
            self._last[episode_id] = _LastWrite(current, percent)
            return True
        return False

    def forget(self, episode_id: str) -> None:
        """清掉记录，避免长连接上字典无界增长。"""
        self._last.pop(episode_id, None)


__all__ = ['MIN_INTERVAL_SECONDS', 'MIN_PERCENT_DELTA', 'ProgressThrottle']
