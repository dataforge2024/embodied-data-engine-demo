"""上传进度推送与节流。

500MB 文件按 256KB 分片有 2000 片，每片都推一帧会把 WS 淹掉，而 SysOps 看
进度条并不需要 2000 次更新。因此按时间节流：同一文件间隔 ≥1s 才推。

两个例外必须推送，否则进度条会停在中途：
- 第一片（让界面立刻从 0 动起来）
- 最后一片（100% 必须准确到达）

连接断开期间直接丢弃，不排队 —— 重连后补推一堆历史进度没有意义，
下一片的进度天然覆盖它们。
"""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_SECONDS = 1.0


@dataclass
class ProgressThrottle:
    """按文件跟踪上次推送时间，决定当前这片是否该推。

    只做决策，不做 IO —— 便于单测不起 WS。
    """

    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS
    _last_sent_at: dict[str, float] = field(default_factory=dict)

    def should_send(
        self, episode_id: str, *, uploaded_parts: int, total_parts: int, now: float | None = None
    ) -> bool:
        """判断这一片的进度是否该推送。

        Args:
            episode_id: Episode ID，节流按它分组。
            uploaded_parts: 已完成分片数。
            total_parts: 总分片数。
            now: 当前时间（秒），测试注入用。

        Returns:
            True 表示该推。
        """
        current = time.monotonic() if now is None else now

        # 首片与末片无条件推送
        if uploaded_parts <= 1 or uploaded_parts >= total_parts:
            self._last_sent_at[episode_id] = current
            return True

        last = self._last_sent_at.get(episode_id)
        if last is None or current - last >= self.min_interval_seconds:
            self._last_sent_at[episode_id] = current
            return True
        return False

    def forget(self, episode_id: str) -> None:
        """文件处理完后清掉记录，避免长跑进程里字典无界增长。"""
        self._last_sent_at.pop(episode_id, None)


__all__ = ['DEFAULT_MIN_INTERVAL_SECONDS', 'ProgressThrottle']
