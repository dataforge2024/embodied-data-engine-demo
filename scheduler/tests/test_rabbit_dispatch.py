"""薄消费层与 Celery 映射。

这里不起 broker —— 测的是「契约怎么翻译成 Celery 任务」，以及注册表新增事件时能不能被发现。
真 broker 上的失败路径由 ``make demo-rabbit`` 与 testing/ 的集成测试覆盖。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from rdh_contract.enums import JobType
from rdh_contract.events import (
    EVENT_MODELS,
    EVENT_REGISTRY,
    AnnotationApproved,
    DatasetBuildRequested,
    EpisodeRejected,
    EpisodeUploaded,
    routing_keys_for_queue,
)

from scheduler.celery_app import TASK_BY_ROUTING_KEY, build_celery_app
from scheduler.config import Settings
from scheduler.consumers.event import ConsumedEvent, UndecodableEvent, decode_envelope
from scheduler.consumers.rabbit import RabbitConsumer
from scheduler.rabbit_worker import RabbitWorker

pytestmark = pytest.mark.unit


def uploaded_payload() -> dict[str, Any]:
    """一条合法的 ``episode.uploaded`` payload（JSON 形式）。"""
    return {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "episode_id": "ep-1",
        "task_id": "task-1",
        "object_key": "episodes/ep-1.mcap",
        "size_bytes": 1024,
        "checksum": "a" * 64,
        "recorded_topics": ["/camera/rgb"],
    }


def envelope(routing_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """构造与 Platform ``prepare_event()`` 同形状的信封。"""
    return {
        "routing_key": routing_key,
        "exchange": "robotdatahub.events",
        "event_id": payload.get("event_id", str(uuid.uuid4())),
        "published_at": datetime.now(UTC).isoformat(),
        "payload": payload,
    }


class _FakeTask:
    """记录 delay 调用的假 Celery task。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    def delay(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)


class TestEnvelopeDecoding:
    """信封解码 —— 两个后端共用这一份。"""

    def test_valid_envelope_decodes(self) -> None:
        """合法信封解出 routing_key、event_id 与 payload 模型。"""
        payload = uploaded_payload()
        routing_key, event_id, decoded = decode_envelope(envelope("episode.uploaded", payload))
        assert routing_key == "episode.uploaded"
        assert event_id == payload["event_id"]
        assert isinstance(decoded, EpisodeUploaded)
        assert decoded.episode_id == "ep-1"

    def test_unregistered_routing_key_is_undecodable(self) -> None:
        """未注册的 routing_key 不该重试 —— 抛 UndecodableEvent 让调用方转死信。"""
        with pytest.raises(UndecodableEvent, match="未注册"):
            decode_envelope(envelope("episode.teleported", uploaded_payload()))

    def test_missing_routing_key_is_undecodable(self) -> None:
        """缺 routing_key 的信封无法路由。"""
        raw = envelope("episode.uploaded", uploaded_payload())
        del raw["routing_key"]
        with pytest.raises(UndecodableEvent, match="routing_key"):
            decode_envelope(raw)

    def test_payload_violating_contract_is_undecodable(self) -> None:
        """payload 少字段 → 不合契约。重试格式错误的消息永远不会成功。"""
        payload = uploaded_payload()
        del payload["checksum"]
        with pytest.raises(UndecodableEvent, match="不合契约"):
            decode_envelope(envelope("episode.uploaded", payload))

    def test_extra_field_rejected(self) -> None:
        """多出未声明字段也算契约漂移 —— 模型是 extra=forbid。"""
        payload = uploaded_payload() | {"surprise": 1}
        with pytest.raises(UndecodableEvent, match="不合契约"):
            decode_envelope(envelope("episode.uploaded", payload))


class TestTaskMapping:
    """routing_key → Celery task 的映射必须跟得上契约。"""

    def test_every_registered_event_has_a_task(self) -> None:
        """契约新增事件时这条会红 —— 否则新事件会静默无人处理。"""
        assert set(TASK_BY_ROUTING_KEY) == set(EVENT_REGISTRY)

    def test_max_retries_comes_from_contract(self) -> None:
        """每个 task 的重试上限取自契约声明，不在代码里硬编码。"""
        for routing_key, task in TASK_BY_ROUTING_KEY.items():
            assert task.max_retries == EVENT_REGISTRY[routing_key].max_retries

    def test_celery_queues_do_not_collide_with_event_queues(self) -> None:
        """Celery 队列与领域事件队列必须分开。

        Celery protocol v2 的消息体是 ``[args, kwargs, embed]``，领域事件是信封格式。
        两者混进同一个队列，消费方会把对方的消息当成垃圾丢掉。
        """
        app = build_celery_app(Settings())
        celery_queues = {route["queue"] for route in app.conf.task_routes.values()}
        event_queues = {queue.value for queue in JobType}
        assert not (celery_queues & event_queues)

    def test_task_routes_cover_every_task(self) -> None:
        """每个 task 都有显式路由，不落到默认队列。"""
        app = build_celery_app(Settings())
        routed = set(app.conf.task_routes)
        assert {task.name for task in TASK_BY_ROUTING_KEY.values()} == routed


class TestConsumerTopology:
    """队列与绑定取自契约。"""

    def test_queue_name_is_job_type(self) -> None:
        """队列名取自 JobType —— 与 KEDA 的 queueName 同源。"""
        consumer = RabbitConsumer(amqp_url="amqp://guest:guest@127.0.0.1:5672/", queue=JobType.TOOL)
        assert consumer.queue_name == JobType.TOOL.value

    def test_bindings_come_from_contract(self) -> None:
        """绑定 key 取自 ``routing_keys_for_queue()``，不硬编码。"""
        for queue in JobType:
            consumer = RabbitConsumer(amqp_url="amqp://localhost/", queue=queue)
            assert consumer.subscribed == routing_keys_for_queue(queue)

    def test_every_event_is_bound_by_exactly_one_queue(self) -> None:
        """并集等于全集且不重叠 —— 没有事件无人订阅或被重复消费。"""
        bound: list[str] = []
        for queue in JobType:
            bound.extend(RabbitConsumer(amqp_url="amqp://localhost/", queue=queue).subscribed)
        assert sorted(bound) == sorted(EVENT_REGISTRY)
        assert len(bound) == len(set(bound))


class TestDispatch:
    """薄消费层的分派。"""

    async def test_dispatch_delegates_to_celery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """校验通过的事件转成 Celery 任务，payload 以 JSON 形式传过去。"""
        fake = _FakeTask("scheduler.ingest_episode")
        monkeypatch.setitem(TASK_BY_ROUTING_KEY, "episode.uploaded", fake)

        payload = uploaded_payload()
        _, event_id, decoded = decode_envelope(envelope("episode.uploaded", payload))
        worker = RabbitWorker(queue=JobType.INGEST, settings=Settings(queue_backend="rabbit"))
        await worker.dispatch(
            ConsumedEvent(
                routing_key="episode.uploaded", event_id=event_id, payload=decoded, handle=None
            )
        )

        assert len(fake.calls) == 1
        assert fake.calls[0]["episode_id"] == "ep-1"

    async def test_unmapped_routing_key_warns_instead_of_crashing(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """没有映射的事件记警告 —— 不静默丢弃，也不炸掉消费循环。"""
        monkeypatch.delitem(TASK_BY_ROUTING_KEY, "episode.uploaded")
        payload = uploaded_payload()
        _, event_id, decoded = decode_envelope(envelope("episode.uploaded", payload))
        worker = RabbitWorker(queue=JobType.INGEST, settings=Settings(queue_backend="rabbit"))

        with caplog.at_level(logging.WARNING):
            await worker.dispatch(
                ConsumedEvent(
                    routing_key="episode.uploaded", event_id=event_id, payload=decoded, handle=None
                )
            )

        assert "无对应 Celery task" in caplog.text


class TestPayloadModelsMatchRegistry:
    """注册表声明的模型名与实际类一致 —— 删事件后剩下的正是这四个。"""

    def test_registry_models_are_the_four_live_events(self) -> None:
        """algo.completed / algo.failed 删掉后，注册表只剩这四个模型。"""
        assert set(EVENT_MODELS.values()) == {
            EpisodeUploaded,
            EpisodeRejected,
            AnnotationApproved,
            DatasetBuildRequested,
        }

    def test_dead_algo_events_are_gone(self) -> None:
        """两个死事件不该再能被查到。"""
        assert "algo.completed" not in EVENT_REGISTRY
        assert "algo.failed" not in EVENT_REGISTRY
