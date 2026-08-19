"""队列巡检 —— 运维页要看的队列深度与死信数。

两个后端各查一遍同样的东西：每个队列的待消费条数、死信总数、绑定的 routing_key。
拓扑一律取自契约（``JobType`` 与 ``routing_keys_for_queue``），不硬编码队列名 ——
队列名同时是 KEDA 的 ``queueName``，两处写死必然漂移。

**不 import scheduler。** 依赖铁律不允许，所以这里自己用 aio-pika 被动声明查深度，
而不是复用 ``RabbitConsumer``。代价是一小段重复的 AMQP 代码，换来的是模块不耦合。
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from rdh_contract.enums import JobType
from rdh_contract.events import EXCHANGE_DLX, EXCHANGE_MAIN, routing_keys_for_queue

logger = logging.getLogger(__name__)

# 与 Scheduler 的 consumers/rabbit.py::DLQ_NAME 一致。
# 这是一处有意的重复：跨模块共享常量得进契约，而死信队列名是部署约定而非契约内容。
DLQ_NAME = "dead-letter"


@dataclass(frozen=True)
class QueueDepth:
    """单个队列的巡检结果。"""

    queue: str
    """队列名 —— 与 KEDA 的 ``queueName`` 同源。"""

    pending: int
    """待消费条数。"""

    routing_keys: tuple[str, ...]
    """本队列订阅的 routing_key，取自契约。"""

    reachable: bool = True
    """队列是否存在。``file`` 后端下目录不存在也算可达（深度 0）。"""


@dataclass(frozen=True)
class QueueSnapshot:
    """一次完整巡检。"""

    backend: str
    queues: tuple[QueueDepth, ...]
    dlq_count: int
    exchange: str
    dlx: str
    broker: str | None = None
    """脱敏后的 broker 地址；``file`` 后端为 ``None``。"""

    error: str | None = None
    """巡检失败原因。broker 连不上时给出人话，而不是抛 500。"""


def _file_queue_depth(queue_dir: Path, queue: str) -> int:
    """文件队列的待消费条数 —— 数 ``*.json``，跳过写入中的临时文件。"""
    target = queue_dir / queue
    if not target.is_dir():
        return 0
    return len([p for p in target.glob("*.json") if not p.name.startswith(".")])


def inspect_file_queues(*, queue_dir: Path, dlq_dir: Path) -> QueueSnapshot:
    """巡检文件队列后端。"""
    queues = tuple(
        QueueDepth(
            queue=job.value,
            pending=_file_queue_depth(queue_dir, job.value),
            routing_keys=routing_keys_for_queue(job),
        )
        for job in JobType
    )
    # 死信按队列分目录，全部加起来
    dlq_total = sum(_file_queue_depth(dlq_dir, job.value) for job in JobType)
    return QueueSnapshot(
        backend="file",
        queues=queues,
        dlq_count=dlq_total,
        exchange=EXCHANGE_MAIN,
        dlx=EXCHANGE_DLX,
    )


async def inspect_rabbit_queues(*, amqp_url: str, broker_label: str) -> QueueSnapshot:
    """巡检 RabbitMQ 后端。

    用 ``passive=True`` 被动声明取深度：只查不建，队列不存在就标 ``reachable=False``
    而不是顺手创建 —— 运维页不该有副作用。

    **每次查询开一条独立 channel。** ``aio_pika`` 按 channel 缓存 ``Queue`` 对象，
    同一条 channel 上重复声明拿回的是首次声明时的实例，``message_count`` 会永远停在
    那一刻的值。
    """
    import aio_pika

    empty = tuple(
        QueueDepth(
            queue=job.value,
            pending=0,
            routing_keys=routing_keys_for_queue(job),
            reachable=False,
        )
        for job in JobType
    )

    try:
        connection = await aio_pika.connect_robust(amqp_url)
    except Exception as exc:
        logger.warning("broker 不可达，队列巡检降级：%s", exc)
        return QueueSnapshot(
            backend="rabbit",
            queues=empty,
            dlq_count=0,
            exchange=EXCHANGE_MAIN,
            dlx=EXCHANGE_DLX,
            broker=broker_label,
            error=f"broker 不可达：{exc}",
        )

    async def count(name: str) -> tuple[int, bool]:
        """取队列深度，队列不存在返回 ``(0, False)``。"""
        try:
            async with connection.channel() as channel:
                declared = await channel.declare_queue(name, durable=True, passive=True)
                return int(declared.declaration_result.message_count or 0), True
        except Exception:
            # 队列还没被 Scheduler 声明过 —— 未起 worker 时的正常状态，不是错误
            return 0, False

    try:
        depths: list[QueueDepth] = []
        for job in JobType:
            pending, reachable = await count(job.value)
            depths.append(
                QueueDepth(
                    queue=job.value,
                    pending=pending,
                    routing_keys=routing_keys_for_queue(job),
                    reachable=reachable,
                )
            )
        dlq_total, _ = await count(DLQ_NAME)
    finally:
        await connection.close()

    return QueueSnapshot(
        backend="rabbit",
        queues=tuple(depths),
        dlq_count=dlq_total,
        exchange=EXCHANGE_MAIN,
        dlx=EXCHANGE_DLX,
        broker=broker_label,
    )


__all__ = [
    "DLQ_NAME",
    "QueueDepth",
    "QueueSnapshot",
    "inspect_file_queues",
    "inspect_rabbit_queues",
]
