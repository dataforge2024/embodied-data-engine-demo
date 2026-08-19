"""RabbitMQ 事件发布器（交互⑤的生产实现）。

与 :class:`~app.services.event_publisher.FileQueuePublisher` 实现同一个
:class:`~app.services.event_publisher.EventPublisher` 协议，信封格式也共用
``prepare_event()`` —— 切后端时调用方与消费方都不用改。

**只声明 exchange，不声明队列。** 投递目标由 binding 决定，队列与绑定是 Scheduler
启动时声明的：发布方不该知道有哪些消费队列。这与文件队列有意不同 ——
``FileQueuePublisher`` 按 ``consumer_queue`` 建目录，等价于发布方在决定投递目标。

消息以 ``PERSISTENT`` 投递，配 durable exchange —— broker 重启不丢已接收的消息。
"""

import json
import logging

import aio_pika
from rdh_contract.events import EXCHANGE_MAIN
from rdh_contract.schemas.base import ContractModel

from app.services.event_publisher import prepare_event

logger = logging.getLogger(__name__)


class RabbitPublisher:
    """基于 RabbitMQ 的事件发布器。

    连接惰性建立并复用：首次 :meth:`publish` 时连接并声明 exchange。``aio_pika`` 的
    ``connect_robust`` 会在断线后自动重连，无需自己写重试。
    """

    def __init__(self, amqp_url: str, *, exchange_name: str = EXCHANGE_MAIN) -> None:
        self._amqp_url = amqp_url
        self._exchange_name = exchange_name
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def _ensure_exchange(self) -> aio_pika.abc.AbstractExchange:
        """惰性连接并声明主 exchange。"""
        if self._exchange is not None:
            return self._exchange

        self._connection = await aio_pika.connect_robust(self._amqp_url)
        channel = await self._connection.channel()
        self._exchange = await channel.declare_exchange(
            self._exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=True,
        )
        logger.info("已连接 broker 并声明 exchange=%s", self._exchange_name)
        return self._exchange

    async def publish(self, routing_key: str, payload: ContractModel) -> str:
        """校验并发布一条事件，返回 ``event_id``。

        broker 不可达时抛异常 —— 调用方的事务不会 commit，Agent 会重试上传回调。
        代价是 broker 故障会阻塞上传链路（见 change 的 design.md「已知不足」）。
        """
        event_id, envelope, spec = prepare_event(routing_key, payload)
        exchange = await self._ensure_exchange()

        message = aio_pika.Message(
            body=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            message_id=event_id,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await exchange.publish(message, routing_key=routing_key)
        logger.debug(
            "事件已发布 routing_key=%s event_id=%s queue=%s",
            routing_key,
            event_id,
            spec.consumer_queue.value,
        )
        return event_id

    async def close(self) -> None:
        """关闭连接。应用停机时调用。"""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._exchange = None


__all__ = ["RabbitPublisher"]
