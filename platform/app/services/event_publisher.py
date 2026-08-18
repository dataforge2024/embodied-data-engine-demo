"""事件发布 —— Platform 唯一的消息出口（交互⑤）。

本地 demo 用**文件队列**替代 RabbitMQ：每条消息写成 ``<queue_dir>/<queue>/<seq>-<uuid>.json``。
消费方（Scheduler）扫目录取文件。语义上保留 RabbitMQ 的关键性质：

- 按 ``EVENT_REGISTRY`` 的 ``consumer_queue`` 分目录，等价于 topic exchange 的绑定
- 消息含 ``event_id``，消费方据此幂等去重
- 落盘用「临时文件 + 原子 rename」，避免消费方读到写一半的内容

生产替换点只有 :class:`FileQueuePublisher`：换成 aio-pika 实现同样的 :class:`EventPublisher`
协议即可，调用方无需改动。
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from rdh_contract.events import EVENT_MODELS, EVENT_REGISTRY, get_spec
from rdh_contract.schemas.base import ContractModel


class UnregisteredEventError(ValueError):
    """试图发布未在契约中注册的事件。"""


class EventPublisher(Protocol):
    """事件发布器协议。生产实现用 RabbitMQ，本地实现用文件队列。"""

    async def publish(self, routing_key: str, payload: ContractModel) -> str:
        """发布一条事件，返回 ``event_id``。"""
        ...


class FileQueuePublisher:
    """基于文件系统的事件发布器（本地替身）。"""

    def __init__(self, queue_dir: Path) -> None:
        self._queue_dir = queue_dir

    async def publish(self, routing_key: str, payload: ContractModel) -> str:
        """校验并投递事件。

        校验两件事：routing_key 已注册，payload 类型与注册表声明一致。
        错发的事件在这里就被拦住，而不是让 Scheduler 反序列化失败。
        """
        spec = get_spec(routing_key)
        expected_model = EVENT_MODELS[routing_key]
        if not isinstance(payload, expected_model):
            raise UnregisteredEventError(
                f"{routing_key} 期望 payload 类型 {expected_model.__name__}，"
                f"实际收到 {type(payload).__name__}"
            )

        event_id = getattr(payload, "event_id", None) or str(uuid.uuid4())
        target_dir = self._queue_dir / spec.consumer_queue.value
        target_dir.mkdir(parents=True, exist_ok=True)

        envelope = {
            "routing_key": routing_key,
            "exchange": spec.exchange,
            "event_id": event_id,
            "published_at": datetime.now(UTC).isoformat(),
            "payload": payload.model_dump(mode="json"),
        }

        # 时间戳前缀保证消费顺序大致等于发布顺序
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        final_path = target_dir / f"{stamp}-{event_id}.json"
        tmp_path = target_dir / f".{final_path.name}.tmp"
        tmp_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.rename(final_path)  # 原子：消费方不会读到半个文件
        return event_id

    def pending_count(self, queue: str) -> int:
        """某队列的待消费消息数（调试与断言用）。"""
        target = self._queue_dir / queue
        return len(list(target.glob("*.json"))) if target.is_dir() else 0


class NullPublisher:
    """丢弃所有事件。测试中隔离消息副作用时使用。"""

    def __init__(self) -> None:
        self.published: list[tuple[str, ContractModel]] = []

    async def publish(self, routing_key: str, payload: ContractModel) -> str:
        """记录而不真正投递。"""
        get_spec(routing_key)  # 仍校验注册
        self.published.append((routing_key, payload))
        return getattr(payload, "event_id", None) or str(uuid.uuid4())


def known_routing_keys() -> tuple[str, ...]:
    """已注册的 routing_key，供健康检查暴露。"""
    return tuple(sorted(EVENT_REGISTRY))


__all__ = [
    "EventPublisher",
    "FileQueuePublisher",
    "NullPublisher",
    "UnregisteredEventError",
    "known_routing_keys",
]
