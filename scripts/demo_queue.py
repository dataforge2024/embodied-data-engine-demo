"""demo 的队列适配层：一套调用，两个后端。

demo.py 是线性叙事，不该在每个阶段都写一遍「if 文件队列 else RabbitMQ」。这里把两个后端
包成同一组 ``depth`` / ``fetch`` / ``ack`` / ``dlq_count``，让 ``make demo`` 与
``make demo-rabbit`` 走同一份剧本 —— 剧本能同时跑通两个后端，本身就是「切后端不用改调用方」
的证据。

统一成 async：文件队列的 ``fetch`` / ``ack`` 是同步的，RabbitMQ 的是异步的，包一层省得
调用方分情况。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from rdh_contract.enums import JobType


class _Consumer(Protocol):
    """两个后端消费者的公共形状（仅 demo 用到的部分）。"""

    def fetch(self) -> Any: ...
    def ack(self, event: Any) -> Any: ...


class DemoQueue:
    """把发布器与 4 个队列的消费者包成一组统一调用。"""

    def __init__(self, *, backend: str, publisher: Any, consumers: dict[str, Any]) -> None:
        self._backend = backend
        self._publisher = publisher
        self._consumers = consumers
        self._acked = 0

    @property
    def backend(self) -> str:
        """当前后端名。"""
        return self._backend

    @property
    def is_rabbit(self) -> bool:
        """是否走真 broker。"""
        return self._backend == "rabbit"

    @property
    def publisher(self) -> Any:
        """事件发布器，直接交给 Platform 的服务层。"""
        return self._publisher

    @property
    def substitute_note(self) -> str:
        """替身说明，打在 demo 开头。"""
        real = "真 RabbitMQ" if self.is_rabbit else "文件队列 ← RabbitMQ"
        return f"SQLite ← PostgreSQL / 本地目录 ← MinIO / {real} / 子进程 ← K8s Job"

    async def depth(self, queue: str) -> int:
        """某队列的待消费条数。"""
        if self.is_rabbit:
            return int(await self._consumers[queue].depth())
        return int(self._publisher.pending_count(queue))

    async def fetch(self, queue: str) -> Any:
        """从某队列取一条事件；空则返回 ``None``。"""
        consumer = self._consumers[queue]
        if self.is_rabbit:
            return await consumer.fetch()
        return consumer.fetch()

    async def ack(self, queue: str, event: Any) -> None:
        """确认处理成功。"""
        consumer = self._consumers[queue]
        if self.is_rabbit:
            await consumer.ack(event)
        else:
            consumer.ack(event)
        self._acked += 1

    async def dlq_count(self) -> int:
        """死信条数。"""
        if self.is_rabbit:
            return int(await self._consumers[JobType.INGEST.value].dlq_count())
        return int(self._consumers[JobType.INGEST.value].dlq_count())

    async def processed_count(self) -> int:
        """已处理条数。

        文件队列有 ``processed/`` 归档目录可数；RabbitMQ 下 ack 后消息即消失（这是接受的
        语义差异，排查靠日志与 Platform 的状态轨迹），所以退化成数本进程 ack 过几条。
        """
        if self.is_rabbit:
            return self._acked
        return int(self._consumers[JobType.INGEST.value].processed_count())

    async def purge(self) -> None:
        """清空队列与死信，保证每次 demo 从干净状态起步。

        文件队列由 demo 删 ``.runtime`` 目录负责；RabbitMQ 的数据在 broker 里，
        上一次跑剩下的消息会让本次断言错位，所以要显式清。
        """
        if not self.is_rabbit:
            return
        for consumer in self._consumers.values():
            await consumer.purge()

    async def close(self) -> None:
        """关闭连接。"""
        if not self.is_rabbit:
            return
        await self._publisher.close()
        for consumer in self._consumers.values():
            await consumer.close()


def build_demo_queue(*, backend: str, amqp_url: str, queue_dir: Path, sched: Any) -> DemoQueue:
    """按后端构造适配层。

    ``sched`` 是 Scheduler 的 Settings —— 文件队列后端要用它的 dlq / processed 目录。
    """
    if backend == "rabbit":
        from app.services.rabbit_publisher import RabbitPublisher
        from scheduler.consumers.rabbit import RabbitConsumer

        return DemoQueue(
            backend=backend,
            publisher=RabbitPublisher(amqp_url),
            consumers={
                queue.value: RabbitConsumer(amqp_url=amqp_url, queue=queue) for queue in JobType
            },
        )

    from app.services.event_publisher import FileQueuePublisher
    from scheduler.consumers.queue import FileQueueConsumer

    return DemoQueue(
        backend=backend,
        publisher=FileQueuePublisher(queue_dir),
        consumers={
            queue.value: FileQueueConsumer(
                queue_dir=sched.event_queue_dir,
                dlq_dir=sched.dlq_dir,
                processed_dir=sched.processed_dir,
                queue_name=queue.value,
            )
            for queue in JobType
        },
    )


__all__ = ["DemoQueue", "build_demo_queue"]
