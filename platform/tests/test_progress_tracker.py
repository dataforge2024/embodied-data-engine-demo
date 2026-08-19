"""进度节流落库测试（tasks.md #11.1）。

只测节流决策 —— 落库本身走 EpisodeRepository，已有覆盖。
"""

from app.services.progress_tracker import ProgressThrottle


class TestFirstAndFinal:
    """首帧与末帧必须落库。"""

    def test_first_frame_writes(self) -> None:
        throttle = ProgressThrottle()
        assert throttle.should_write('ep1', uploaded_parts=1, total_parts=100, now=0.0)

    def test_final_frame_always_writes(self) -> None:
        """100% 必须准确落库，否则看板停在中途。"""
        throttle = ProgressThrottle()
        throttle.should_write('ep1', uploaded_parts=1, total_parts=100, now=0.0)
        # 距上次仅 0.01s、增量 99% —— 时间条件不满足但末片强制写
        assert throttle.should_write('ep1', uploaded_parts=100, total_parts=100, now=0.01)


class TestThrottling:
    """中间帧按时间或增量节流。"""

    def test_rapid_frames_suppressed(self) -> None:
        throttle = ProgressThrottle()
        assert throttle.should_write('ep1', uploaded_parts=1, total_parts=1000, now=0.0)
        # 0.1s 后、增量 0.1% —— 两个条件都不满足
        assert not throttle.should_write('ep1', uploaded_parts=2, total_parts=1000, now=0.1)
        assert not throttle.should_write('ep1', uploaded_parts=3, total_parts=1000, now=0.2)

    def test_interval_triggers_write(self) -> None:
        """满 2 秒即写，即便增量很小。"""
        throttle = ProgressThrottle()
        throttle.should_write('ep1', uploaded_parts=1, total_parts=1000, now=0.0)
        assert throttle.should_write('ep1', uploaded_parts=5, total_parts=1000, now=2.1)

    def test_percent_delta_triggers_write(self) -> None:
        """增量满 5% 即写，即便时间很短。"""
        throttle = ProgressThrottle()
        throttle.should_write('ep1', uploaded_parts=1, total_parts=100, now=0.0)
        # 0.1s 后但涨了 6%
        assert throttle.should_write('ep1', uploaded_parts=7, total_parts=100, now=0.1)

    def test_episodes_throttled_independently(self) -> None:
        throttle = ProgressThrottle()
        assert throttle.should_write('ep1', uploaded_parts=1, total_parts=1000, now=0.0)
        assert throttle.should_write('ep2', uploaded_parts=1, total_parts=1000, now=0.0)
        assert not throttle.should_write('ep1', uploaded_parts=2, total_parts=1000, now=0.1)
        assert not throttle.should_write('ep2', uploaded_parts=2, total_parts=1000, now=0.1)


class TestForget:
    """长连接上不能让字典无界增长。"""

    def test_forget_resets_state(self) -> None:
        throttle = ProgressThrottle()
        throttle.should_write('ep1', uploaded_parts=1, total_parts=1000, now=0.0)
        assert not throttle.should_write('ep1', uploaded_parts=2, total_parts=1000, now=0.1)

        throttle.forget('ep1')
        # 忘掉后视作首帧，重新写
        assert throttle.should_write('ep1', uploaded_parts=3, total_parts=1000, now=0.2)

    def test_forget_unknown_is_noop(self) -> None:
        ProgressThrottle().forget('never-seen')
