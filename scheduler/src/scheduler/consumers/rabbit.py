"""RabbitMQ 事件消费（交互⑥的生产实现）。

与 :class:`~scheduler.consumers.queue.FileQueueConsumer` 暴露同样的 ``fetch`` / ``ack`` /
``reject`` 三个动作，信封解码共用 :func:`~scheduler.consumers.event.decode_envelope` ——
:mod:`scheduler.pipelines` 不用改。

**队列与绑定由消费方声明。** 绑定 key 取自契约的 ``routing_keys_for_queue()``，队列名取自
``JobType`` —— 与 KEDA 的 ``queueName`` 同源。发布方只声明 exchange。

死信走 ``EXCHANGE_DLX``：队列声明时挂 ``x-dead-letter-exchange``，``reject(requeue=False)``
的消息由 broker 投进死信 exchange，不需要自己搬。
"""

import json
import logging
from typing import Any

import aio_pika
from rdh_contract.enums import JobType
from rdh_contract.events import (
    EXCHANGE_DLX,
    EXCHANGE_MAIN,
    get_spec,
    routing_keys_for_queue,
)

from scheduler.consumers.event import ConsumedEvent, UndecodableEvent, decode_envelope

logger = logging.getLogger(__name__)

# 死信队列名：所有队列的死信汇到一处，便于人工排查
DLQ_NAME = "dead-letter"


class RabbitConsumer:
    """基于 RabbitMQ 的事件消费者。

    ``fetch`` 用 ``basic_get`` 拉取单条消息（与文件队列的轮询语义一致），而不是 push 式
    ``consume``。POC 阶段这样能让 ``drain()`` 那套「处理完当前积压就返回」的逻辑不用改；
    生产的吞吐来自多副本 worker，不是单进程的 prefetch。
    """

    def __init__(
        self,
        *,
        amqp_url: str,
        queue: JobType,
        exchange_name: str = EXCHANGE_MAIN,
        dlx_name: str = EXCHANGE_DLX,
        dlq_name: str = DLQ_NAME,
        queue_name: str | None = None,
    ) -> None:
        self._amqp_url = amqp_url
        self._queue = queue
        self._exchange_name = exchange_name
        self._dlx_name = dlx_name
        self._dlq_name = dlq_name
        self._queue_name = queue_name or queue.value
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._amqp_queue: aio_pika.abc.AbstractQueue | None = None
        self._attempts: dict[str, int] = {}

    @property
    def queue_name(self) -> str:
        """队列名 —— 默认取 ``JobType``，与 KEDA 的 ``queueName`` 同源。

        ``queue_name`` 参数只给测试用：拓扑名带上随机前缀才能让并发的测试互不干扰。
        生产不要传。
        """
        return self._queue_name

    @property
    def subscribed(self) -> tuple[str, ...]:
        """本队列订阅的 routing_key，取自契约。"""
        return routing_keys_for_queue(self._queue)

    async def declare(self) -> aio_pika.abc.AbstractQueue:
        """声明拓扑：exchange、死信 exchange、队列、绑定。幂等，可重复调用。"""
        if self._amqp_queue is not None:
            return self._amqp_queue

        self._connection = await aio_pika.connect_robust(self._amqp_url)
        self._channel = await self._connection.channel()

        # 一次只取一条，与文件队列的逐条处理语义一致
        await self._channel.set_qos(prefetch_count=1)

        exchange = await self._channel.declare_exchange(
            self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
        )
        dlx = await self._channel.declare_exchange(
            self._dlx_name, aio_pika.ExchangeType.TOPIC, durable=True
        )

        # 死信队列：绑 # 收全部死信，便于人工排查
        dlq = await self._channel.declare_queue(self._dlq_name, durable=True)
        await dlq.bind(dlx, routing_key="#")

        queue = await self._channel.declare_queue(
            self.queue_name,
            durable=True,
            arguments={"x-dead-letter-exchange": self._dlx_name},
        )
        for routing_key in self.subscribed:
            await queue.bind(exchange, routing_key=routing_key)

        self._amqp_queue = queue
        logger.info(
            "队列已声明 queue=%s 绑定 %s",
            self.queue_name,
            ", ".join(self.subscribed) or "（无）",
        )
        return queue

    async def fetch(self) -> ConsumedEvent | None:
        """取一条事件；队列为空返回 ``None``。

        不合契约的消息直接 ``nack(requeue=False)`` 进死信 —— 重试一条格式错误的消息永远
        不会成功，不该占用重试预算。这与 :class:`FileQueueConsumer.fetch` 行为一致。

        转死信后**继续取下一条**而不是返回 ``None``：返回 ``None`` 等于告诉调用方队列空了，
        一条坏消息就会挡住后面所有好消息（``FileQueueConsumer`` 的 ``for`` 循环同理）。
        """
        queue = await self.declare()

        while (message := await queue.get(fail=False, timeout=None)) is not None:
            try:
                raw: dict[str, Any] = json.loads(message.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                logger.warning("消息无法解析，转入死信：%s", exc)
                await message.nack(requeue=False)
                continue

            try:
                routing_key, event_id, payload = decode_envelope(raw)
            except UndecodableEvent as exc:
                logger.warning("事件不合契约，转入死信：%s", exc)
                await message.nack(requeue=False)
                continue

            return ConsumedEvent(
                routing_key=routing_key,
                event_id=event_id,
                payload=payload,
                attempt=self._attempts.get(event_id, 0) + 1,
                raw=raw,
                handle=message,
            )
        return None

    async def ack(self, event: ConsumedEvent) -> None:
        """确认处理成功。"""
        self._attempts.pop(event.event_id, None)
        await self._message_of(event).ack()

    async def dead_letter(self, event: ConsumedEvent, *, reason: str) -> None:
        """无人处理的消息进死信，不算「已处理」。

        契约要求「worker 不得确认一条它没有实际处理的消息」—— ack 之后消息即消失，
        订阅了却无人处理的事件就此静默丢弃。``nack(requeue=False)`` 让 broker 按
        ``x-dead-letter-exchange`` 投进死信，证据留得住。
        """
        logger.error(
            "事件无人处理，转入死信 routing_key=%s event_id=%s reason=%s",
            event.routing_key,
            event.event_id,
            reason,
        )
        self._attempts.pop(event.event_id, None)
        await self._message_of(event).nack(requeue=False)

    async def reject(self, event: ConsumedEvent, *, reason: str) -> bool:
        """处理失败。返回 True 表示会重投，False 表示已进死信。

        重试上限取契约里该事件的 ``max_retries``。重投用 ``requeue=True`` 立即回队列 ——
        延迟退避由 Celery 的 ``retry_backoff`` 负责（见 :mod:`scheduler.celery_app`），
        这一层只管「还给不给机会」。
        """
        limit = get_spec(event.routing_key).max_retries
        self._attempts[event.event_id] = event.attempt
        message = self._message_of(event)

        if event.attempt > limit:
            logger.error(
                "事件重试耗尽转入死信 event_id=%s attempt=%d/%d reason=%s",
                event.event_id,
                event.attempt,
                limit,
                reason,
            )
            await message.nack(requeue=False)
            return False

        logger.warning(
            "事件处理失败将重投 event_id=%s attempt=%d/%d reason=%s",
            event.event_id,
            event.attempt,
            limit,
            reason,
        )
        await message.nack(requeue=True)
        return True

    async def depth(self) -> int:
        """队列深度（调试与断言用）—— KEDA 用的也是这个指标。"""
        return await self._message_count(self.queue_name)

    async def dlq_count(self) -> int:
        """死信数量。"""
        return await self._message_count(self._dlq_name)

    async def purge(self) -> None:
        """清空本队列与死信队列。

        只给 demo 与测试用 —— 上一次跑剩下的消息会让本次断言错位。文件队列后端靠删
        ``.runtime`` 目录达到同样效果，但 broker 里的数据在容器卷里，得显式清。
        """
        queue = await self.declare()
        await queue.purge()
        assert self._channel is not None
        dlq = await self._channel.declare_queue(self._dlq_name, durable=True)
        await dlq.purge()
        self._attempts.clear()

    async def _message_count(self, queue_name: str) -> int:
        """被动声明取队列深度。

        **必须开一条独立 channel。** ``aio_pika`` 按 channel 缓存 ``Queue`` 对象，在同一条
        channel 上重复 ``declare_queue`` 拿回的是首次声明时的实例，它的
        ``declaration_result.message_count`` 永远是首次声明那一刻的值（通常是 0）——
        复用 channel 会让深度读数永远停在旧基线上。
        """
        await self.declare()
        assert self._connection is not None
        async with self._connection.channel() as channel:
            declared = await channel.declare_queue(queue_name, durable=True, passive=True)
            return int(declared.declaration_result.message_count or 0)

    async def close(self) -> None:
        """关闭连接。"""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None
            self._amqp_queue = None

    @staticmethod
    def _message_of(event: ConsumedEvent) -> aio_pika.abc.AbstractIncomingMessage:
        """取出 RabbitMQ 后端存在 ``handle`` 里的原始消息。"""
        message = event.handle
        if not isinstance(message, aio_pika.abc.AbstractIncomingMessage):
            actual = type(message).__name__
            raise TypeError(f"RabbitMQ 后端期望 handle 是 IncomingMessage，实际是 {actual}")
        return message


__all__ = ["DLQ_NAME", "RabbitConsumer"]
