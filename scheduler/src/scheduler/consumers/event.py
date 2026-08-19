"""消费侧的事件表示与信封解码。

文件队列与 RabbitMQ 共用这一份结构：Platform 的 ``prepare_event()`` 产出同一种信封，
两个后端只是搬运方式不同。解码逻辑收口在这里，避免两份实现对「什么算不合契约」有分歧。
"""

from dataclasses import dataclass, field
from typing import Any

from rdh_contract.events import get_model
from rdh_contract.schemas.base import ContractModel


@dataclass(frozen=True)
class ConsumedEvent:
    """一条已取出、已通过契约校验的事件。

    ``handle`` 是后端特定的引用，供 ``ack`` / ``reject`` 定位这条消息：文件队列里是
    :class:`~pathlib.Path`，RabbitMQ 下是 ``IncomingMessage``。消费逻辑不该碰它。
    """

    routing_key: str
    event_id: str
    payload: ContractModel
    attempt: int = 1
    raw: dict[str, Any] = field(default_factory=dict)
    handle: Any = None


class UndecodableEvent(ValueError):
    """信封无法解码或 payload 不合契约。

    这类消息**不该重试** —— 重试一条格式错误的消息永远不会成功，只会耗尽重试预算。
    直接进死信。
    """


def decode_envelope(raw: dict[str, Any]) -> tuple[str, str, ContractModel]:
    """按契约解码信封，返回 ``(routing_key, event_id, payload)``。

    不合契约时抛 :class:`UndecodableEvent`，调用方据此转死信。
    """
    routing_key = raw.get("routing_key", "")
    event_id = raw.get("event_id", "")
    if not routing_key:
        raise UndecodableEvent("信封缺少 routing_key")

    try:
        model = get_model(routing_key)
    except KeyError as exc:
        raise UndecodableEvent(f"未注册的 routing_key：{routing_key}") from exc

    try:
        payload = model.model_validate(raw.get("payload"))
    except Exception as exc:
        raise UndecodableEvent(f"{routing_key} 的 payload 不合契约：{exc}") from exc

    return routing_key, event_id, payload


__all__ = ["ConsumedEvent", "UndecodableEvent", "decode_envelope"]
