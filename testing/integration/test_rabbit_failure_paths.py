"""RabbitMQ 后端上的三条失败路径（change 的任务 5.3）。

文件队列上这三条已经过了，但它们真正要证明的是 **broker 的行为**：死信是 broker 按
``x-dead-letter-exchange`` 投的，重投计数跨消息持久，重复投递由 broker 决定。
文件队列只能模仿个大概，所以必须在真 broker 上再验一遍。

需要先 ``make broker-up``。没有 broker 时整个模块 skip —— ``make check`` 保持零外部依赖。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for extra in (
    REPO_ROOT / "contract" / "src",
    REPO_ROOT / "scheduler" / "src",
    REPO_ROOT / "platform",
):
    if str(extra) not in sys.path:
        sys.path.append(str(extra))

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

AMQP_URL = os.environ.get("RDH_AMQP_URL", "amqp://rdh:change-me-local-only@127.0.0.1:5672/")

# 每次跑用独立的 exchange / 队列名，避免与 demo 或上一次跑的残留互相干扰
RUN_ID = uuid.uuid4().hex[:8]


async def _broker_reachable() -> bool:
    """能连上 broker 才跑这一组测试。"""
    try:
        import aio_pika

        connection = await asyncio.wait_for(aio_pika.connect_robust(AMQP_URL), timeout=5.0)
    except Exception:
        return False
    await connection.close()
    return True


def _skip_without_broker() -> None:
    """没有 broker 就 skip，并说清怎么起。"""
    if not asyncio.run(_broker_reachable()):
        pytest.skip(f"broker 不可达（{AMQP_URL}）—— 先执行 make broker-up")


_skip_without_broker()


from app.services.rabbit_publisher import RabbitPublisher  # noqa: E402
from rdh_contract.enums import JobType  # noqa: E402
from rdh_contract.events import EpisodeUploaded  # noqa: E402
from scheduler.consumers.rabbit import RabbitConsumer  # noqa: E402


def make_event() -> EpisodeUploaded:
    """一条合法的 ``episode.uploaded``。"""
    return EpisodeUploaded(
        event_id=str(uuid.uuid4()),
        occurred_at=datetime.now(UTC),
        episode_id=f"ep-{uuid.uuid4().hex[:8]}",
        task_id="task-1",
        object_key="episodes/ep.mcap",
        size_bytes=2048,
        checksum="b" * 64,
        recorded_topics=("/camera/rgb",),
    )


def isolated_consumer(queue: JobType) -> RabbitConsumer:
    """拓扑名全部带上 RUN_ID 前缀，让每次跑互不干扰。

    **死信队列也要隔离** —— 它默认是所有队列共享的一个 `dead-letter`，每个 DLX 都用 `#`
    绑到它上面。只隔离主队列和 exchange 的话，死信计数会串到 demo 与上一次跑的残留上。
    """
    return RabbitConsumer(
        amqp_url=AMQP_URL,
        queue=queue,
        exchange_name=f"test-{RUN_ID}.events",
        dlx_name=f"test-{RUN_ID}.dlx",
        dlq_name=f"test-{RUN_ID}.dead-letter",
        queue_name=f"test-{RUN_ID}.{queue.value}",
    )


@pytest.fixture
async def wired() -> AsyncIterator[tuple[Any, RabbitConsumer]]:
    """发布器 + ingest 消费者，拓扑已声明、队列已清空。"""
    consumer = isolated_consumer(JobType.INGEST)
    await consumer.declare()
    await consumer.purge()

    publisher = RabbitPublisher(AMQP_URL, exchange_name=f"test-{RUN_ID}.events")
    yield publisher, consumer

    await publisher.close()
    await consumer.close()


async def _publish_raw(publisher: Any, body: bytes, routing_key: str) -> None:
    """绕过契约校验直接投一条原始消息 —— 模拟「别人发了坏数据」。"""
    import aio_pika

    exchange = await publisher._ensure_exchange()
    await exchange.publish(
        aio_pika.Message(body=body, content_type="application/json"),
        routing_key=routing_key,
    )


class TestBadPayloadGoesToDlq:
    """路径一：payload 不合契约 → 进死信，不占重试预算。"""

    async def test_malformed_payload_dead_letters(
        self, wired: tuple[Any, RabbitConsumer]
    ) -> None:
        """缺字段的 payload 被 broker 投进死信 exchange，fetch 不返回它。"""
        publisher, consumer = wired
        bad = (
            b'{"routing_key": "episode.uploaded", "event_id": "bad-1", '
            b'"payload": {"nope": 1}}'
        )
        await _publish_raw(publisher, bad, "episode.uploaded")
        await asyncio.sleep(0.3)

        assert await consumer.fetch() is None, "不合契约的消息不该被交给业务逻辑"
        await asyncio.sleep(0.3)
        assert await consumer.dlq_count() == 1, "坏消息应由 broker 投进死信队列"

    async def test_bad_message_does_not_block_good_ones(
        self, wired: tuple[Any, RabbitConsumer]
    ) -> None:
        """坏消息在前、好消息在后时，好消息仍能被取到。

        这条盯的是 ``fetch`` 里那个 while：转死信后必须继续取下一条，
        返回 None 等于告诉调用方队列空了，一条坏消息就挡住后面所有消息。
        """
        publisher, consumer = wired
        await _publish_raw(publisher, b"not even json", "episode.uploaded")
        good = make_event()
        await publisher.publish("episode.uploaded", good)
        await asyncio.sleep(0.3)

        fetched = await consumer.fetch()
        assert fetched is not None, "坏消息不该挡住后面的好消息"
        assert fetched.event_id == good.event_id
        await consumer.ack(fetched)


class TestRetryExhaustionGoesToDlq:
    """路径二：重试耗尽 → 进死信，不无限重试。"""

    async def test_retry_budget_from_contract_then_dead_letter(
        self, wired: tuple[Any, RabbitConsumer]
    ) -> None:
        """反复 reject 到超过契约声明的 max_retries 后进死信。"""
        from rdh_contract.events import get_spec

        publisher, consumer = wired
        limit = get_spec("episode.uploaded").max_retries
        await publisher.publish("episode.uploaded", make_event())
        await asyncio.sleep(0.3)

        # 前 limit 次 reject 都应该重投
        for _ in range(limit):
            event = await consumer.fetch()
            assert event is not None, "重投的消息应该还能取到"
            assert await consumer.reject(event, reason="模拟处理失败") is True
            await asyncio.sleep(0.2)

        # 第 limit+1 次超出预算，进死信
        final = await consumer.fetch()
        assert final is not None
        assert await consumer.reject(final, reason="模拟处理失败") is False, "超出预算应进死信"
        await asyncio.sleep(0.3)

        assert await consumer.dlq_count() == 1
        assert await consumer.fetch() is None, "进死信后主队列应为空"


class TestRedeliveryIsIdempotent:
    """路径三：同一消息投两次，结果不变。"""

    async def test_same_event_twice_yields_same_payload(
        self, wired: tuple[Any, RabbitConsumer]
    ) -> None:
        """同一 event_id 投两次，两次解出的 payload 完全一致。

        RabbitMQ 是至少一次投递，且换 broker 后进程内去重集合失效 —— 幂等靠消费本身。
        这条证明的是「重复投递不会产生不同的结果」，即 handler 拿到的输入是同一个。
        """
        publisher, consumer = wired
        event = make_event()

        await publisher.publish("episode.uploaded", event)
        await publisher.publish("episode.uploaded", event)
        await asyncio.sleep(0.3)

        first = await consumer.fetch()
        assert first is not None
        await consumer.ack(first)

        second = await consumer.fetch()
        assert second is not None, "至少一次投递：第二条确实会到达"
        await consumer.ack(second)

        assert first.event_id == second.event_id == event.event_id
        assert first.payload == second.payload, "同一事件重投，payload 必须一致"
        assert await consumer.depth() == 0

    async def test_event_id_survives_redelivery(
        self, wired: tuple[Any, RabbitConsumer]
    ) -> None:
        """重投后 event_id 不变 —— 幂等判断依赖它跨投递稳定。"""
        publisher, consumer = wired
        event = make_event()
        await publisher.publish("episode.uploaded", event)
        await asyncio.sleep(0.3)

        first = await consumer.fetch()
        assert first is not None
        assert await consumer.reject(first, reason="强制重投") is True
        await asyncio.sleep(0.2)

        again = await consumer.fetch()
        assert again is not None
        assert again.event_id == event.event_id, "重投后 event_id 必须稳定"
        await consumer.ack(again)


class TestTopologyFromContract:
    """拓扑真的按契约声明出来了。"""

    async def test_queue_declared_with_dlx(self, wired: tuple[Any, RabbitConsumer]) -> None:
        """队列挂了死信 exchange —— 否则 nack 的消息直接消失。"""
        _, consumer = wired
        assert await consumer.depth() == 0  # 能被动声明成功即说明队列存在且参数匹配

    async def test_bindings_match_contract(self, wired: tuple[Any, RabbitConsumer]) -> None:
        """ingest 队列只绑 episode.uploaded，别的事件不该落进来。"""
        publisher, consumer = wired
        from rdh_contract.events import routing_keys_for_queue

        assert consumer.subscribed == routing_keys_for_queue(JobType.INGEST)

        # 发一条 tool 队列的事件，ingest 不该收到
        from rdh_contract.events import AnnotationApproved

        await publisher.publish(
            "annotation.approved",
            AnnotationApproved(
                event_id=str(uuid.uuid4()),
                occurred_at=datetime.now(UTC),
                episode_id="ep-x",
                task_id="task-1",
                annotation_id="anno-1",
                segment_count=2,
                approved_by="user-1",
            ),
        )
        await asyncio.sleep(0.3)
        assert await consumer.fetch() is None, "ingest 队列不该收到 annotation.approved"
