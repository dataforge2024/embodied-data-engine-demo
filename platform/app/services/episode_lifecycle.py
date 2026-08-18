"""Episode 状态流转 —— **唯一入口**。

架构约束（openspec/project.md）：所有 Episode 状态变更必须经本模块，由它调用 contract 的
:func:`~rdh_contract.state_machine.assert_transition` 做守卫。Repository 的
``apply_transition`` 不做合法性判断，绕过本模块直接调它就绕过了状态机。

两条纪律：

1. **先落库再发事件**。反过来会让 Scheduler 消费到 Platform 还查不到的 Episode。
2. **重放识别为幂等而非报错**。RabbitMQ 至少一次投递，同一事件可能重复到达；
   目标状态已经是当前状态时视为「已处理」，返回现状而不抛异常。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from rdh_contract.enums import EpisodeStatus
from rdh_contract.events import AnnotationApproved, EpisodeRejected, EpisodeUploaded
from rdh_contract.schemas import Episode
from rdh_contract.state_machine import InvalidTransitionError, assert_transition, is_terminal

from app.repositories.episode import EpisodeRepository
from app.services.event_publisher import EventPublisher


@dataclass(frozen=True)
class TransitionOutcome:
    """状态流转结果。

    ``changed=False`` 表示这是一次重放（目标状态已达成），调用方应返回 200 而非 409。
    """

    episode: Episode
    changed: bool
    published_event_id: str | None = None


class EpisodeNotFoundError(KeyError):
    """Episode 不存在。上层转 404。"""


class EpisodeLifecycleService:
    """Episode 生命周期编排。"""

    def __init__(self, *, episodes: EpisodeRepository, publisher: EventPublisher) -> None:
        self._episodes = episodes
        self._publisher = publisher

    async def _require(self, episode_id: str) -> Episode:
        """取 Episode，不存在抛 :class:`EpisodeNotFoundError`。"""
        episode = await self._episodes.find_by_id(episode_id)
        if episode is None:
            raise EpisodeNotFoundError(f"Episode 不存在：{episode_id}")
        return episode

    async def transition(
        self,
        episode_id: str,
        *,
        target: EpisodeStatus,
        reject_reason: str | None = None,
    ) -> TransitionOutcome:
        """执行一次状态迁移，非法则抛 :class:`InvalidTransitionError`（上层转 409）。"""
        current = await self._require(episode_id)

        if current.status is target:
            # 重放：已经是目标状态，视为已处理
            return TransitionOutcome(episode=current, changed=False)

        assert_transition(current.status, target)
        updated = await self._episodes.apply_transition(
            episode_id, target=target, reject_reason=reject_reason
        )
        return TransitionOutcome(episode=updated, changed=True)

    async def mark_uploaded(
        self,
        episode_id: str,
        *,
        object_key: str,
        size_bytes: int,
        checksum: str,
        duration_ms: int,
        recorded_topics: tuple[str, ...],
    ) -> TransitionOutcome:
        """交互③：上传完成 → ``uploaded``，随后发布 ``episode.uploaded``（交互⑤）。"""
        current = await self._require(episode_id)

        if current.status is EpisodeStatus.UPLOADED:
            return TransitionOutcome(episode=current, changed=False)

        assert_transition(current.status, EpisodeStatus.UPLOADED)
        await self._episodes.attach_upload_result(
            episode_id,
            object_key=object_key,
            size_bytes=size_bytes,
            checksum=checksum,
            duration_ms=duration_ms,
        )
        episode = await self._episodes.apply_transition(episode_id, target=EpisodeStatus.UPLOADED)

        # 状态已落库，再发事件
        event_id = await self._publisher.publish(
            "episode.uploaded",
            EpisodeUploaded(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(UTC),
                episode_id=episode_id,
                task_id=episode.task_id,
                object_key=object_key,
                size_bytes=size_bytes,
                checksum=checksum,
                recorded_topics=recorded_topics,
            ),
        )
        return TransitionOutcome(episode=episode, changed=True, published_event_id=event_id)

    async def start_processing(self, episode_id: str) -> TransitionOutcome:
        """Scheduler 开始处理 → ``processing``。"""
        return await self.transition(episode_id, target=EpisodeStatus.PROCESSING)

    async def finish_processing(
        self, episode_id: str, *, succeeded: bool, error_message: str | None = None
    ) -> TransitionOutcome:
        """交互⑧：流水线结束 → ``verification_pending`` 或 ``failed``。"""
        if succeeded:
            return await self.transition(episode_id, target=EpisodeStatus.VERIFICATION_PENDING)
        return await self.transition(
            episode_id,
            target=EpisodeStatus.FAILED,
            reject_reason=error_message or "算子流水线失败",
        )

    async def reject(
        self, episode_id: str, *, reason: str, rejected_by: str, task_id: str | None = None
    ) -> TransitionOutcome:
        """核验打回 → ``rejected``（终态），并发布 ``episode.rejected``。"""
        outcome = await self.transition(
            episode_id, target=EpisodeStatus.REJECTED, reject_reason=reason
        )
        if not outcome.changed:
            return outcome

        event_id = await self._publisher.publish(
            "episode.rejected",
            EpisodeRejected(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(UTC),
                episode_id=episode_id,
                task_id=task_id or outcome.episode.task_id,
                reason=reason,
                rejected_by=rejected_by,
            ),
        )
        return TransitionOutcome(episode=outcome.episode, changed=True, published_event_id=event_id)

    async def publish_episode(
        self, episode_id: str, *, annotation_id: str, segment_count: int, approved_by: str
    ) -> TransitionOutcome:
        """审核通过 → ``published``，并发布 ``annotation.approved``。"""
        outcome = await self.transition(episode_id, target=EpisodeStatus.PUBLISHED)
        if not outcome.changed:
            return outcome

        event_id = await self._publisher.publish(
            "annotation.approved",
            AnnotationApproved(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(UTC),
                episode_id=episode_id,
                task_id=outcome.episode.task_id,
                annotation_id=annotation_id,
                segment_count=segment_count,
                approved_by=approved_by,
            ),
        )
        return TransitionOutcome(episode=outcome.episode, changed=True, published_event_id=event_id)

    async def assert_actionable(self, episode_id: str, *, expected: EpisodeStatus) -> Episode:
        """校验 Episode 处于预期状态，否则抛 :class:`InvalidTransitionError`。

        用于人工操作前的前置检查：终态 Episode 不接受任何操作。
        """
        episode = await self._require(episode_id)
        if episode.status is not expected:
            raise InvalidTransitionError(episode.status, expected)
        return episode

    async def is_finalized(self, episode_id: str) -> bool:
        """Episode 是否已到终态。"""
        return is_terminal((await self._require(episode_id)).status)


__all__ = ["EpisodeLifecycleService", "EpisodeNotFoundError", "TransitionOutcome"]
