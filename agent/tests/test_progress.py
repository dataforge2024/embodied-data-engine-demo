"""测试进度推送节流。"""

from agent.progress import ProgressThrottle


class TestProgressThrottle:
    """节流决策逻辑。"""

    def test_first_and_last_always_sent(self) -> None:
        """首片与末片无条件推送。"""
        throttle = ProgressThrottle()
        assert throttle.should_send('ep1', uploaded_parts=1, total_parts=100, now=0.0)
        assert throttle.should_send('ep1', uploaded_parts=100, total_parts=100, now=0.1)

    def test_middle_parts_throttled(self) -> None:
        """中间分片按时间节流（默认 1s）。"""
        throttle = ProgressThrottle()
        # 第 1 片：推
        assert throttle.should_send('ep1', uploaded_parts=1, total_parts=100, now=0.0)
        # 第 2 片（0.5s 后）：不推
        assert not throttle.should_send('ep1', uploaded_parts=2, total_parts=100, now=0.5)
        # 第 3 片（再过 0.6s = 距上次 1.1s）：推
        assert throttle.should_send('ep1', uploaded_parts=3, total_parts=100, now=1.1)
        # 第 4 片（0.2s 后）：不推
        assert not throttle.should_send('ep1', uploaded_parts=4, total_parts=100, now=1.3)

    def test_different_episodes_tracked_separately(self) -> None:
        """不同 episode 独立节流。"""
        throttle = ProgressThrottle()
        assert throttle.should_send('ep1', uploaded_parts=1, total_parts=50, now=0.0)
        assert throttle.should_send('ep2', uploaded_parts=1, total_parts=50, now=0.0)
        # ep1 第 2 片间隔不够，不推
        assert not throttle.should_send('ep1', uploaded_parts=2, total_parts=50, now=0.5)
        # ep2 第 2 片间隔够了，推
        assert throttle.should_send('ep2', uploaded_parts=2, total_parts=50, now=1.1)

    def test_custom_interval(self) -> None:
        """可配置节流间隔。"""
        throttle = ProgressThrottle(min_interval_seconds=2.0)
        assert throttle.should_send('ep1', uploaded_parts=1, total_parts=100, now=0.0)
        assert not throttle.should_send('ep1', uploaded_parts=2, total_parts=100, now=1.5)
        assert throttle.should_send('ep1', uploaded_parts=3, total_parts=100, now=2.1)

    def test_forget_clears_tracking(self) -> None:
        """forget 清掉记录，避免字典无界增长。"""
        throttle = ProgressThrottle()
        throttle.should_send('ep1', uploaded_parts=1, total_parts=10, now=0.0)
        assert 'ep1' in throttle._last_sent_at
        throttle.forget('ep1')
        assert 'ep1' not in throttle._last_sent_at
        # forget 不存在的 key 不报错
        throttle.forget('ep999')
