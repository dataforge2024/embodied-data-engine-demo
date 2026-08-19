"""RabbitMQ 后端的薄消费层。

职责只有一件：**把领域事件翻译成 Celery 任务**。

按 routing_key 查契约拿模型 → 校验（在 :class:`RabbitConsumer.fetch` 里完成）→
``task.delay()``。四个队列共用这一份代码，靠 ``TASK_BY_ROUTING_KEY`` 配置驱动。

**校验放在进 Celery 之前是有意的**：不合契约的 payload 不该占用重试预算 ——
重试一条格式错误的消息永远不会成功。这与 ``FileQueueConsumer.fetch()`` 现有行为一致。

与 :class:`~scheduler.worker.Worker`（进程内文件队列）的区别只在搬运层：这里 ack 的含义是
「任务已交给 Celery」，真正的执行与重试由 Celery task 负责。
"""

import asyncio
import logging

from rdh_contract.enums import JobType

from scheduler.celery_app import TASK_BY_ROUTING_KEY
from scheduler.config import Settings, get_settings
from scheduler.consumers.event import ConsumedEvent
from scheduler.consumers.rabbit import RabbitConsumer

logger = logging.getLogger(__name__)


class RabbitWorker:
    """单个队列的薄消费循环：消费领域事件，投递 Celery 任务。"""

    def __init__(self, *, queue: JobType, settings: Settings) -> None:
        self._queue = queue
        self._settings = settings
        self._consumer = RabbitConsumer(amqp_url=settings.amqp_url, queue=queue)

    @property
    def consumer(self) -> RabbitConsumer:
        """暴露消费者，供测试与断言。"""
        return self._consumer

    async def dispatch(self, event: ConsumedEvent) -> str | None:
        """把事件交给对应的 Celery task。

        返回 ``None`` 表示已投递；返回字符串表示**没有实际处理**，字符串是原因 ——
        调用方据此转死信而不是 ack。契约要求 worker 不得确认一条它没处理的消息。
        """
        task = TASK_BY_ROUTING_KEY.get(event.routing_key)
        if task is None:
            # ack 掉等于静默丢弃，所以交回调用方转死信
            return f"无对应 Celery task：{event.routing_key}"

        # payload 已按契约校验过，转 JSON 交给 Celery（跨进程只能传可序列化的值）
        task.delay(event.payload.model_dump(mode="json"))
        logger.info(
            "已投递 Celery 任务 task=%s routing_key=%s event_id=%s",
            task.name,
            event.routing_key,
            event.event_id,
        )
        return None

    async def drain(self) -> int:
        """把当前队列里的消息全部转成 Celery 任务，返回条数。"""
        handled = 0
        while (event := await self._consumer.fetch()) is not None:
            try:
                unhandled = await self.dispatch(event)
            except Exception as exc:
                logger.exception("投递 Celery 任务失败 routing_key=%s", event.routing_key)
                await self._consumer.reject(event, reason=str(exc))
                continue
            if unhandled is not None:
                # 没人处理的消息不能 ack —— 那会让它像处理过一样消失
                await self._consumer.dead_letter(event, reason=unhandled)
                continue
            await self._consumer.ack(event)
            handled += 1
        return handled

    async def run_forever(self) -> None:
        """常驻消费循环。"""
        await self._consumer.declare()
        logger.info(
            "rabbit worker 启动 queue=%s 订阅 %s",
            self._consumer.queue_name,
            ", ".join(self._consumer.subscribed) or "（无）",
        )
        while True:
            handled = await self.drain()
            if handled == 0:
                await asyncio.sleep(self._settings.poll_interval_seconds)

    async def close(self) -> None:
        """关闭连接。"""
        await self._consumer.close()


def build_rabbit_workers(settings: Settings | None = None) -> tuple[RabbitWorker, ...]:
    """构造 4 类薄消费层。"""
    settings = settings or get_settings()
    return tuple(RabbitWorker(queue=queue, settings=settings) for queue in JobType)


async def main() -> None:
    """启动全部薄消费层。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    settings = get_settings()
    logger.info("消费 broker=%s", settings.amqp_url_safe)
    workers = build_rabbit_workers(settings)
    try:
        await asyncio.gather(*(w.run_forever() for w in workers))
    finally:
        for worker in workers:
            await worker.close()


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["RabbitWorker", "build_rabbit_workers", "main"]
