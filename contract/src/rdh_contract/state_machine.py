"""Episode 状态机 —— 唯一事实来源。

Platform 侧所有状态变更必须经 ``services/episode_lifecycle.py`` 单一入口，
由该入口调用 :func:`can_transition` 做守卫。Repository 层不得暴露裸的 status 赋值。

设计约束（由 ``tests/test_state_machine.py`` 强制）：

- 每个非终态至少有一条出边
- 终态没有出边
- 每个状态从 :data:`INITIAL_STATE` 可达
- 每个非终态都能走到某个终态（不存在活锁）

主链路共 9 态::

    recording → uploading → uploaded → processing → verification_pending
      → annotation_processing → annotation_pending → annotation_review → published
"""

from collections.abc import Mapping

from .enums import EpisodeStatus

# 初始状态：Agent 开始录制
INITIAL_STATE: EpisodeStatus = EpisodeStatus.RECORDING

# 终态：不再有任何出边
TERMINAL_STATES: frozenset[EpisodeStatus] = frozenset(
    {
        EpisodeStatus.PUBLISHED,
        EpisodeStatus.REJECTED,
        EpisodeStatus.FAILED,
    }
)

# 合法状态迁移。key 为源状态，value 为允许的目标状态集合。
#
# 每条边的触发方标注在注释里，便于排查「谁改了状态」。
EPISODE_TRANSITIONS: Mapping[EpisodeStatus, frozenset[EpisodeStatus]] = {
    # Agent 录制结束开始上传；录制中断直接判失败
    EpisodeStatus.RECORDING: frozenset(
        {
            EpisodeStatus.UPLOADING,
            EpisodeStatus.FAILED,
        }
    ),
    # Agent 分片上传完成后 HTTP 回调（交互③）；上传彻底失败判 FAILED
    EpisodeStatus.UPLOADING: frozenset(
        {
            EpisodeStatus.UPLOADED,
            EpisodeStatus.FAILED,
        }
    ),
    # Platform 发布 episode.uploaded，Scheduler 消费后进入处理
    EpisodeStatus.UPLOADED: frozenset(
        {
            EpisodeStatus.PROCESSING,
            EpisodeStatus.FAILED,
        }
    ),
    # Scheduler 回调处理结果（交互⑧）：解析+预标注完成进核验队列
    EpisodeStatus.PROCESSING: frozenset(
        {
            EpisodeStatus.VERIFICATION_PENDING,
            EpisodeStatus.FAILED,
        }
    ),
    # 人工核验（Tool，交互④）：通过进送标处理，打回则终止
    #
    # 注意这里不是直连 ANNOTATION_PENDING。质检通过后要先过一个异步的送标处理环节，
    # 理由见 openspec/changes/archive/2026-08-21-manual-workflow-progression/design.md 第 1 节。
    EpisodeStatus.VERIFICATION_PENDING: frozenset(
        {
            EpisodeStatus.ANNOTATION_PROCESSING,
            EpisodeStatus.REJECTED,
        }
    ),
    # 送标处理（Scheduler 异步，系统推进）：完成进标注队列，算子失败判 FAILED
    EpisodeStatus.ANNOTATION_PROCESSING: frozenset(
        {
            EpisodeStatus.ANNOTATION_PENDING,
            EpisodeStatus.FAILED,
        }
    ),
    # 人工标注（Tool）：提交后进审核
    EpisodeStatus.ANNOTATION_PENDING: frozenset(
        {
            EpisodeStatus.ANNOTATION_REVIEW,
            EpisodeStatus.REJECTED,
        }
    ),
    # 标注审核（Tool）：通过则发布，退回则重做标注（回到 ANNOTATION_PENDING）
    EpisodeStatus.ANNOTATION_REVIEW: frozenset(
        {
            EpisodeStatus.PUBLISHED,
            EpisodeStatus.ANNOTATION_PENDING,
            EpisodeStatus.REJECTED,
        }
    ),
    # 终态
    EpisodeStatus.PUBLISHED: frozenset(),
    EpisodeStatus.REJECTED: frozenset(),
    EpisodeStatus.FAILED: frozenset(),
}


class InvalidTransitionError(ValueError):
    """非法状态迁移。

    Platform 应捕获此异常并转成 409 Conflict，避免把内部状态细节泄露给调用方。
    """

    def __init__(self, source: EpisodeStatus, target: EpisodeStatus) -> None:
        self.source = source
        self.target = target
        allowed = sorted(s.value for s in allowed_transitions(source))
        detail = ", ".join(allowed) if allowed else "无（终态）"
        super().__init__(
            f"Episode 不能从 {source.value} 迁移到 {target.value}；允许的目标状态：{detail}"
        )


def allowed_transitions(source: EpisodeStatus) -> frozenset[EpisodeStatus]:
    """返回 ``source`` 允许迁移到的状态集合。终态返回空集。"""
    return EPISODE_TRANSITIONS.get(source, frozenset())


def can_transition(source: EpisodeStatus, target: EpisodeStatus) -> bool:
    """判断 ``source`` → ``target`` 是否为合法迁移。"""
    return target in allowed_transitions(source)


def assert_transition(source: EpisodeStatus, target: EpisodeStatus) -> None:
    """校验迁移合法性，非法则抛 :class:`InvalidTransitionError`。

    这是 Platform ``episode_lifecycle`` 守卫的推荐调用方式。
    """
    if not can_transition(source, target):
        raise InvalidTransitionError(source, target)


def is_terminal(status: EpisodeStatus) -> bool:
    """判断是否为终态。"""
    return status in TERMINAL_STATES


__all__ = [
    "EPISODE_TRANSITIONS",
    "INITIAL_STATE",
    "TERMINAL_STATES",
    "InvalidTransitionError",
    "allowed_transitions",
    "assert_transition",
    "can_transition",
    "is_terminal",
]
