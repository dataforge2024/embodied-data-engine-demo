"""事件消费（交互⑥）。

本地用文件队列替代 RabbitMQ，但保留消费语义中真正要紧的部分：

- **幂等去重**：按 ``event_id`` 记录已处理集合。至少一次投递下同一事件会重复到达。
- **重试与死信**：失败重试到契约声明的 ``max_retries``，耗尽后进死信目录而非无限重试。
- **消费顺序**：文件名带时间戳前缀，按名排序即大致按发布顺序。

生产替换点是 :class:`FileQueueConsumer`：换成 aio-pika 实现同样的 ``fetch`` / ``ack`` /
``reject`` 三个动作即可，:mod:`scheduler.pipelines` 无需改动。
"""

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rdh_contract.events import get_model, get_spec
from rdh_contract.schemas.base import ContractModel

logger = logging.getLogger(__name__)


@dataclass
class ConsumedEvent:
    """一条已取出的事件。"""

    path: Path
    routing_key: str
    event_id: str
    payload: ContractModel
    attempt: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


class FileQueueConsumer:
    """基于文件系统的事件消费者（本地替身）。"""

    def __init__(
        self,
        *,
        queue_dir: Path,
        dlq_dir: Path,
        processed_dir: Path,
        queue_name: str,
    ) -> None:
        self._queue_path = queue_dir / queue_name
        self._dlq_path = dlq_dir / queue_name
        self._processed_path = processed_dir / queue_name
        self._queue_name = queue_name
        self._seen: set[str] = set()
        self._attempts: dict[str, int] = {}

    @property
    def queue_name(self) -> str:
        """队列名。"""
        return self._queue_name

    def pending(self) -> list[Path]:
        """待消费文件，按名排序（≈ 按发布顺序）。"""
        if not self._queue_path.is_dir():
            return []
        return sorted(p for p in self._queue_path.glob("*.json") if not p.name.startswith("."))

    def fetch(self) -> ConsumedEvent | None:
        """取一条事件。已处理过的（重放）直接归档并跳过。

        解析失败的消息立刻进死信 —— 重试一条格式错误的消息没有意义。
        """
        for path in self.pending():
            try:
                raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("消息无法解析，转入死信：%s", path.name)
                self._move(path, self._dlq_path)
                continue

            routing_key = raw.get("routing_key", "")
            event_id = raw.get("event_id", "")

            if event_id in self._seen:
                logger.info("重放事件已跳过 event_id=%s", event_id)
                self._move(path, self._processed_path)
                continue

            try:
                model = get_model(routing_key)
                payload = model.model_validate(raw["payload"])
            except Exception:
                logger.warning("事件 payload 不合契约，转入死信：%s", routing_key)
                self._move(path, self._dlq_path)
                continue

            return ConsumedEvent(
                path=path,
                routing_key=routing_key,
                event_id=event_id,
                payload=payload,
                attempt=self._attempts.get(event_id, 0) + 1,
                raw=raw,
            )
        return None

    def ack(self, event: ConsumedEvent) -> None:
        """确认处理成功：记入已处理集合并归档。"""
        self._seen.add(event.event_id)
        self._attempts.pop(event.event_id, None)
        self._move(event.path, self._processed_path)

    def reject(self, event: ConsumedEvent, *, reason: str) -> bool:
        """处理失败。

        返回 True 表示还会重试，False 表示已进死信。重试上限取契约里该事件的
        ``max_retries`` —— 失败类事件不宜多次重试。
        """
        limit = get_spec(event.routing_key).max_retries
        self._attempts[event.event_id] = event.attempt

        if event.attempt > limit:
            logger.error(
                "事件重试耗尽转入死信 event_id=%s attempt=%d/%d reason=%s",
                event.event_id,
                event.attempt,
                limit,
                reason,
            )
            self._seen.add(event.event_id)
            self._move(event.path, self._dlq_path)
            return False

        logger.warning(
            "事件处理失败将重试 event_id=%s attempt=%d/%d reason=%s",
            event.event_id,
            event.attempt,
            limit,
            reason,
        )
        return True

    def dlq_count(self) -> int:
        """死信数量。"""
        return len(list(self._dlq_path.glob("*.json"))) if self._dlq_path.is_dir() else 0

    def processed_count(self) -> int:
        """已处理数量。"""
        return (
            len(list(self._processed_path.glob("*.json"))) if self._processed_path.is_dir() else 0
        )

    def _move(self, path: Path, target_dir: Path) -> None:
        """归档文件。目标已存在同名文件时覆盖（同一 event_id 只保留最后一次）。"""
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(target_dir / path.name))
        except (OSError, shutil.Error) as exc:
            logger.warning("归档失败 %s: %s", path.name, exc)


__all__ = ["ConsumedEvent", "FileQueueConsumer"]
