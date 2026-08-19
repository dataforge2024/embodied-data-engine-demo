"""两个队列后端的共同契约。

`RabbitPublisher` 与 `FileQueuePublisher` 都实现 `EventPublisher`，且共用
`prepare_event()` 的信封格式 —— Scheduler 按同一结构解析两个后端，格式漂移会让切后端时
静默失败。这里测的就是「两边真的一致」以及后端开关选对了实现。

不起真 broker：AMQP 交互用假 exchange 替掉，验证的是信封与拓扑决策，不是网络。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from rdh_contract.enums import JobType
from rdh_contract.events import EXCHANGE_MAIN, EpisodeUploaded

from app.api.dependencies import get_publisher
from app.core.config import Settings
from app.services.event_publisher import (
    FileQueuePublisher,
    UnregisteredEventError,
    prepare_event,
)
from app.services.rabbit_publisher import RabbitPublisher

pytestmark = pytest.mark.unit

ROUTING_KEY = "episode.uploaded"


def make_payload() -> EpisodeUploaded:
    """一条合法的 ``episode.uploaded`` payload。"""
    return EpisodeUploaded(
        event_id=str(uuid.uuid4()),
        occurred_at=datetime.now(UTC),
        episode_id="ep-1",
        task_id="task-1",
        object_key="episodes/ep-1.mcap",
        size_bytes=1024,
        checksum="a" * 64,
        recorded_topics=("/camera/rgb",),
    )


class _FakeExchange:
    """记录发布调用的假 exchange。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, message: Any, routing_key: str) -> None:
        self.published.append((routing_key, message))


class _StubRabbitPublisher(RabbitPublisher):
    """把连接换成假 exchange，其余逻辑（校验、信封、投递参数）保持真实。"""

    def __init__(self) -> None:
        super().__init__("amqp://guest:guest@127.0.0.1:5672/")
        self.exchange = _FakeExchange()

    async def _ensure_exchange(self) -> Any:
        return self.exchange


class TestEnvelopeIsShared:
    """两个后端的信封必须一致。"""

    async def test_both_backends_emit_identical_envelope(self, tmp_path: Path) -> None:
        """同一 payload 经两个后端产出的信封除时间戳外完全一致。"""
        payload = make_payload()

        file_publisher = FileQueuePublisher(tmp_path)
        await file_publisher.publish(ROUTING_KEY, payload)
        written = next((tmp_path / JobType.INGEST.value).glob("*.json"))
        from_file = json.loads(written.read_text(encoding="utf-8"))

        rabbit = _StubRabbitPublisher()
        await rabbit.publish(ROUTING_KEY, payload)
        _, message = rabbit.exchange.published[0]
        from_rabbit = json.loads(message.body.decode("utf-8"))

        from_file.pop("published_at")
        from_rabbit.pop("published_at")
        assert from_file == from_rabbit

    async def test_envelope_carries_contract_topology(self) -> None:
        """信封带 routing_key 与 exchange —— 消费方据此路由，不硬编码。"""
        event_id, envelope, spec = prepare_event(ROUTING_KEY, make_payload())
        assert envelope["routing_key"] == ROUTING_KEY
        assert envelope["exchange"] == EXCHANGE_MAIN
        assert envelope["event_id"] == event_id
        assert spec.consumer_queue is JobType.INGEST

    async def test_event_id_comes_from_payload(self) -> None:
        """``event_id`` 取 payload 自带的值 —— 消费方的幂等依赖它跨重投稳定。"""
        payload = make_payload()
        event_id, envelope, _ = prepare_event(ROUTING_KEY, payload)
        assert event_id == payload.event_id
        assert envelope["event_id"] == payload.event_id


class TestValidationBeforePublish:
    """不合契约的事件在发布方就被拦住。"""

    async def test_unregistered_routing_key_rejected(self) -> None:
        """未注册的 routing_key 发不出去。"""
        with pytest.raises(KeyError):
            prepare_event("episode.teleported", make_payload())

    async def test_payload_type_mismatch_rejected(self) -> None:
        """payload 类型与注册表声明不符时拒绝 —— 不让 Scheduler 反序列化失败。"""
        wrong = make_payload()
        with pytest.raises(UnregisteredEventError):
            prepare_event("annotation.approved", wrong)

    async def test_rabbit_publisher_rejects_before_connecting(self) -> None:
        """校验发生在连接之前：坏事件不会触碰 broker。"""
        publisher = RabbitPublisher("amqp://guest:guest@127.0.0.1:1/")
        with pytest.raises(UnregisteredEventError):
            await publisher.publish("annotation.approved", make_payload())


class TestRabbitDeliveryProperties:
    """AMQP 投递参数。"""

    async def test_message_is_persistent_and_ided(self) -> None:
        """持久化投递 + message_id=event_id，broker 重启不丢、便于追踪。"""
        import aio_pika

        rabbit = _StubRabbitPublisher()
        event_id = await rabbit.publish(ROUTING_KEY, make_payload())
        routing_key, message = rabbit.exchange.published[0]

        assert routing_key == ROUTING_KEY
        assert message.message_id == event_id
        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT
        assert message.content_type == "application/json"

    async def test_publisher_declares_no_queues(self) -> None:
        """发布方只声明 exchange —— 投递目标由 Scheduler 的 binding 决定。"""
        source = Path(__file__).parent.parent / "app/services/rabbit_publisher.py"
        text = source.read_text(encoding="utf-8")
        assert "declare_queue" not in text
        assert "declare_exchange" in text


class TestBackendSwitch:
    """``RDH_QUEUE_BACKEND`` 开关。"""

    def test_file_backend_is_default(self, tmp_path: Path) -> None:
        """默认走文件队列 —— ``make demo`` 零外部依赖。"""
        settings = Settings(event_queue_dir=tmp_path)
        assert settings.queue_backend == "file"
        assert not settings.uses_rabbit
        assert isinstance(get_publisher(settings), FileQueuePublisher)

    def test_rabbit_backend_selected_by_config(self) -> None:
        """``queue_backend=rabbit`` 时换成 RabbitPublisher。"""
        settings = Settings(queue_backend="rabbit")
        assert settings.uses_rabbit
        assert isinstance(get_publisher(settings), RabbitPublisher)

    def test_rabbit_publisher_is_reused_across_calls(self) -> None:
        """同一 URL 复用同一实例 —— 否则每请求新建一条 AMQP 连接。"""
        settings = Settings(queue_backend="rabbit")
        assert get_publisher(settings) is get_publisher(settings)

    def test_unknown_backend_rejected(self) -> None:
        """拼错的后端名不该静默退化成默认行为。"""
        with pytest.raises(ValueError, match="queue_backend"):
            Settings(queue_backend="rabbitmq")

    def test_amqp_url_redacts_password(self) -> None:
        """日志用的连接串不含密码。"""
        settings = Settings(queue_backend="rabbit", amqp_url="amqp://alice:s3cret@broker:5672/vh")
        assert "s3cret" not in settings.amqp_url_safe
        assert settings.amqp_url_safe == "amqp://alice:***@broker:5672/vh"
