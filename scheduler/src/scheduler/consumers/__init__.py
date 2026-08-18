"""事件消费（交互⑥）。"""

from scheduler.consumers.queue import ConsumedEvent, FileQueueConsumer

__all__ = ["ConsumedEvent", "FileQueueConsumer"]
