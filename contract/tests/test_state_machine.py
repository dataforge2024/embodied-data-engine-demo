"""Episode 状态机自洽性测试。

状态机是最容易腐化的契约：加一个状态忘了连边、把终态接出新边、改了流程留下不可达状态。
这些测试保证图本身合法，而不是逐条枚举「a 能到 b」——后者只是把定义抄一遍。
"""

from collections import deque

import pytest

from rdh_contract.enums import EpisodeStatus
from rdh_contract.state_machine import (
    EPISODE_TRANSITIONS,
    INITIAL_STATE,
    TERMINAL_STATES,
    InvalidTransitionError,
    allowed_transitions,
    assert_transition,
    can_transition,
    is_terminal,
)


def reachable_from(start: EpisodeStatus) -> set[EpisodeStatus]:
    """BFS 求可达状态集合（含起点）。"""
    seen = {start}
    queue = deque([start])
    while queue:
        for target in EPISODE_TRANSITIONS[queue.popleft()]:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


@pytest.mark.unit
class TestGraphIntegrity:
    """图结构完整性。"""

    def test_every_status_declared(self) -> None:
        """每个枚举状态都必须在迁移表中声明，否则查表会 KeyError。"""
        assert set(EPISODE_TRANSITIONS) == set(EpisodeStatus)

    def test_targets_are_valid_statuses(self) -> None:
        """所有迁移目标都必须是合法状态。"""
        for source, targets in EPISODE_TRANSITIONS.items():
            for target in targets:
                assert isinstance(target, EpisodeStatus), f"{source} → {target} 不是合法状态"

    def test_no_self_loops(self) -> None:
        """禁止自环：状态不变就不该走迁移流程（幂等由调用方处理）。"""
        for source, targets in EPISODE_TRANSITIONS.items():
            assert source not in targets, f"{source.value} 存在自环"


@pytest.mark.unit
class TestTerminalStates:
    """终态约束。"""

    def test_terminal_states_have_no_outgoing_edges(self) -> None:
        """终态不得有出边，否则「终」态名不副实。"""
        for status in TERMINAL_STATES:
            assert allowed_transitions(status) == frozenset(), f"{status.value} 是终态却有出边"

    def test_non_terminal_states_have_outgoing_edges(self) -> None:
        """非终态必须有出边，否则是死胡同（数据会永久卡住）。"""
        for status in EpisodeStatus:
            if status not in TERMINAL_STATES:
                assert allowed_transitions(status), f"{status.value} 非终态却无出边，数据会卡死"

    def test_is_terminal_matches_terminal_states(self) -> None:
        """``is_terminal`` 与 :data:`TERMINAL_STATES` 一致。"""
        for status in EpisodeStatus:
            assert is_terminal(status) is (status in TERMINAL_STATES)


@pytest.mark.unit
class TestReachability:
    """可达性：无孤岛、无活锁。"""

    def test_all_states_reachable_from_initial(self) -> None:
        """每个状态都能从初始态到达，否则该状态是死代码。"""
        unreachable = set(EpisodeStatus) - reachable_from(INITIAL_STATE)
        assert not unreachable, f"以下状态从 {INITIAL_STATE.value} 不可达：{unreachable}"

    def test_every_state_can_reach_a_terminal(self) -> None:
        """每个状态都能走到某个终态，否则存在活锁（数据永远处理不完）。"""
        for status in EpisodeStatus:
            assert reachable_from(status) & TERMINAL_STATES, f"{status.value} 无法到达任何终态"

    def test_initial_state_is_not_terminal(self) -> None:
        """初始态不能同时是终态。"""
        assert INITIAL_STATE not in TERMINAL_STATES

    def test_published_reachable_without_failure_states(self) -> None:
        """存在一条不经过任何失败态的主链路到 published（正常流程必须走得通）。"""
        happy = {EpisodeStatus.REJECTED, EpisodeStatus.FAILED}
        seen = {INITIAL_STATE}
        queue = deque([INITIAL_STATE])
        while queue:
            for target in EPISODE_TRANSITIONS[queue.popleft()]:
                if target not in seen and target not in happy:
                    seen.add(target)
                    queue.append(target)
        assert EpisodeStatus.PUBLISHED in seen, "主链路走不到 published"


@pytest.mark.unit
class TestDocumentedMainPath:
    """架构文档声明的主链路必须逐跳合法。"""

    MAIN_PATH: tuple[EpisodeStatus, ...] = (
        EpisodeStatus.RECORDING,
        EpisodeStatus.UPLOADING,
        EpisodeStatus.UPLOADED,
        EpisodeStatus.PROCESSING,
        EpisodeStatus.VERIFICATION_PENDING,
        EpisodeStatus.ANNOTATION_PENDING,
        EpisodeStatus.ANNOTATION_REVIEW,
        EpisodeStatus.PUBLISHED,
    )

    def test_main_path_is_walkable(self) -> None:
        """架构文档第二节的 8 态主链路每一跳都合法。"""
        for source, target in zip(self.MAIN_PATH, self.MAIN_PATH[1:], strict=False):
            assert can_transition(source, target), f"主链路断裂：{source.value} → {target.value}"

    def test_review_reject_returns_to_annotation(self) -> None:
        """标注审核退回是回到 annotation_pending 重做，不是进 rejected 终态。"""
        assert can_transition(EpisodeStatus.ANNOTATION_REVIEW, EpisodeStatus.ANNOTATION_PENDING)

    def test_verification_reject_is_terminal(self) -> None:
        """核验打回进 rejected 终态。"""
        assert can_transition(EpisodeStatus.VERIFICATION_PENDING, EpisodeStatus.REJECTED)
        assert is_terminal(EpisodeStatus.REJECTED)

    def test_cannot_skip_verification(self) -> None:
        """不得从 processing 直接跳到标注或发布，绕过人工核验。"""
        assert not can_transition(EpisodeStatus.PROCESSING, EpisodeStatus.ANNOTATION_PENDING)
        assert not can_transition(EpisodeStatus.PROCESSING, EpisodeStatus.PUBLISHED)

    def test_cannot_publish_without_review(self) -> None:
        """只有 annotation_review 能进 published。"""
        sources = [s for s in EpisodeStatus if can_transition(s, EpisodeStatus.PUBLISHED)]
        assert sources == [EpisodeStatus.ANNOTATION_REVIEW]

    def test_cannot_resurrect_terminal_episode(self) -> None:
        """终态 Episode 不可复活（防止后台任务覆盖人工判定）。"""
        for terminal in TERMINAL_STATES:
            for target in EpisodeStatus:
                assert not can_transition(terminal, target), (
                    f"{terminal.value} 可复活到 {target.value}"
                )


@pytest.mark.unit
class TestGuardApi:
    """守卫 API 行为。"""

    def test_assert_transition_passes_on_legal(self) -> None:
        """合法迁移不抛异常。"""
        assert_transition(EpisodeStatus.UPLOADED, EpisodeStatus.PROCESSING)

    def test_assert_transition_raises_on_illegal(self) -> None:
        """非法迁移抛 :class:`InvalidTransitionError`，且带上下文。"""
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(EpisodeStatus.PUBLISHED, EpisodeStatus.PROCESSING)
        err = exc_info.value
        assert err.source is EpisodeStatus.PUBLISHED
        assert err.target is EpisodeStatus.PROCESSING
        assert "published" in str(err)

    def test_invalid_transition_error_is_value_error(self) -> None:
        """继承 ValueError，便于 Platform 统一捕获转 409。"""
        assert issubclass(InvalidTransitionError, ValueError)

    def test_error_message_lists_allowed_targets(self) -> None:
        """错误信息列出允许的目标状态，便于排障。"""
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(EpisodeStatus.UPLOADED, EpisodeStatus.PUBLISHED)
        assert "processing" in str(exc_info.value)

    def test_error_message_for_terminal_source(self) -> None:
        """终态出发的错误信息标明无可用目标。"""
        with pytest.raises(InvalidTransitionError) as exc_info:
            assert_transition(EpisodeStatus.REJECTED, EpisodeStatus.PROCESSING)
        assert "终态" in str(exc_info.value)

    def test_allowed_transitions_returns_frozenset(self) -> None:
        """返回不可变集合，调用方无法意外污染契约。"""
        result = allowed_transitions(EpisodeStatus.UPLOADED)
        assert isinstance(result, frozenset)
