"""WebSocket 连接管理（交互①的服务端侧）。

进程内连接表。多副本部署时需换成 Redis pub/sub 广播 —— 那时本模块的接口不变，
换的是 :meth:`ConnectionManager.push_task` 的投递实现。

下行消息带 ``message_id``，Agent 需回 ack；未 ack 的任务推送在 Agent 重连后由
``/tasks`` 拉取补齐，因此这里不做重投队列。
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from fastapi import WebSocket
from rdh_contract.schemas import TaskRequirement
from rdh_contract.schemas.agent import AgentTaskPush
from rdh_contract.ws import (
    HEARTBEAT_INTERVAL_SECONDS,
    ConsoleAgentStatusFrame,
    ConsoleFrame,
    ConsoleUploadProgressFrame,
    RegisteredFrame,
    TaskCancelFrame,
    TaskPushFrame,
    UploadGrantFrame,
)


@dataclass
class AgentConnection:
    """一个已注册的 Agent 连接。"""

    agent_id: str
    socket: WebSocket
    session_id: str
    connected_at: datetime
    last_seen: datetime
    pending_acks: set[str] = field(default_factory=set)


class ConnectionManager:
    """Agent 连接注册表。"""

    def __init__(self) -> None:
        self._connections: dict[str, AgentConnection] = {}
        # 浏览器控制台连接。无身份键 —— 同一用户可开多个标签页，全部收同样的广播。
        self._consoles: list[WebSocket] = []

    # ---- 控制台（浏览器）----

    def add_console(self, socket: WebSocket) -> None:
        """登记一个浏览器连接。"""
        self._consoles.append(socket)

    def remove_console(self, socket: WebSocket) -> None:
        """摘掉一个浏览器连接。"""
        if socket in self._consoles:
            self._consoles.remove(socket)

    @property
    def console_count(self) -> int:
        """当前浏览器连接数。"""
        return len(self._consoles)

    async def broadcast_console(self, frame: ConsoleFrame) -> int:
        """向所有浏览器推一帧，返回成功条数。

        发送失败的连接就地摘除 —— 浏览器关标签页时 FastAPI 不一定已经触发
        disconnect，留着会让后续广播反复抛错。
        """
        payload = frame.model_dump_json()  # type: ignore[union-attr]
        dead: list[WebSocket] = []
        sent = 0
        for socket in list(self._consoles):
            try:
                await socket.send_text(payload)
                sent += 1
            except Exception:  # noqa: BLE001 —— 连接已断，摘掉即可
                dead.append(socket)
        for socket in dead:
            self.remove_console(socket)
        return sent

    async def notify_agent_status(
        self, agent_id: str, *, online: bool, hostname: str | None = None
    ) -> None:
        """广播 Agent 上下线。"""
        await self.broadcast_console(
            ConsoleAgentStatusFrame(
                agent_id=agent_id,
                online=online,
                hostname=hostname,
                at=datetime.now(UTC),
            )
        )

    async def notify_upload_progress(
        self,
        *,
        episode_id: str,
        agent_id: str,
        uploaded_parts: int,
        total_parts: int,
    ) -> None:
        """广播上传进度。``percent`` 在此算出，前端不再各算一遍。"""
        percent = min(100.0, round(uploaded_parts / total_parts * 100, 1)) if total_parts else 0.0
        await self.broadcast_console(
            ConsoleUploadProgressFrame(
                episode_id=episode_id,
                agent_id=agent_id,
                uploaded_parts=uploaded_parts,
                total_parts=total_parts,
                percent=percent,
            )
        )

    async def register(self, agent_id: str, socket: WebSocket) -> AgentConnection:
        """注册连接并回 ``down.registered``。

        同一 agent_id 重连时替换旧连接：Agent 断电重启后旧连接可能还挂着。
        """
        now = datetime.now(UTC)
        connection = AgentConnection(
            agent_id=agent_id,
            socket=socket,
            session_id=str(uuid.uuid4()),
            connected_at=now,
            last_seen=now,
        )
        self._connections[agent_id] = connection

        await socket.send_text(
            RegisteredFrame(
                message_id=str(uuid.uuid4()),
                session_id=connection.session_id,
                heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
            ).model_dump_json()
        )
        await self.notify_agent_status(agent_id, online=True)
        return connection

    async def disconnect(self, agent_id: str) -> None:
        """移除连接并广播离线。"""
        self._connections.pop(agent_id, None)
        await self.notify_agent_status(agent_id, online=False)

    def touch(self, agent_id: str) -> None:
        """更新最后活跃时间（收到心跳时调用）。"""
        connection = self._connections.get(agent_id)
        if connection is not None:
            connection.last_seen = datetime.now(UTC)

    def is_online(self, agent_id: str) -> bool:
        """是否有活跃连接。"""
        return agent_id in self._connections

    def online_agents(self) -> tuple[str, ...]:
        """在线 Agent 列表。"""
        return tuple(sorted(self._connections))

    def ack(self, agent_id: str, message_id: str) -> bool:
        """处理 Agent 的 ack，返回是否命中一条待确认消息。

        重复 ack 或未知 message_id 返回 False —— 便于观测 Agent 是否在乱发 ack。
        """
        connection = self._connections.get(agent_id)
        if connection is None or message_id not in connection.pending_acks:
            return False
        connection.pending_acks.remove(message_id)
        return True

    async def push_task(
        self, agent_id: str, *, task_id: str, task_name: str, requirement: TaskRequirement
    ) -> bool:
        """推送任务。Agent 离线返回 False（不报错，重连后靠拉取补齐）。"""
        connection = self._connections.get(agent_id)
        if connection is None:
            return False

        message_id = str(uuid.uuid4())
        frame = TaskPushFrame(
            message_id=message_id,
            payload=AgentTaskPush(
                task_id=task_id,
                task_name=task_name,
                requirement=requirement,
                pushed_at=datetime.now(UTC),
            ),
        )
        await connection.socket.send_text(frame.model_dump_json())
        connection.pending_acks.add(message_id)
        return True

    async def push_upload_grant(
        self, agent_id: str, *, episode_id: str, object_key: str, url: str, expires_at: datetime
    ) -> bool:
        """下发上传凭据（交互②前置）。Agent 不持有长期对象存储凭据。"""
        connection = self._connections.get(agent_id)
        if connection is None:
            return False

        frame = UploadGrantFrame(
            message_id=str(uuid.uuid4()),
            episode_id=episode_id,
            object_key=object_key,
            presigned_url=url,
            expires_at=expires_at,
        )
        await connection.socket.send_text(frame.model_dump_json())
        return True

    async def cancel_task(self, agent_id: str, *, task_id: str, reason: str | None = None) -> bool:
        """取消任务。"""
        connection = self._connections.get(agent_id)
        if connection is None:
            return False
        frame = TaskCancelFrame(message_id=str(uuid.uuid4()), task_id=task_id, reason=reason)
        await connection.socket.send_text(frame.model_dump_json())
        return True

    async def trigger_upload(
        self, agent_id: str, *, task_id: str | None = None, reason: str | None = None
    ) -> bool:
        """催促 Agent 重扫并上传（SysOps 手动触发）。"""
        from rdh_contract.ws import UploadTriggerFrame

        connection = self._connections.get(agent_id)
        if connection is None:
            return False
        frame = UploadTriggerFrame(
            message_id=str(uuid.uuid4()), task_id=task_id, reason=reason
        )
        await connection.socket.send_text(frame.model_dump_json())
        return True


@lru_cache
def get_connection_manager() -> ConnectionManager:
    """进程级连接管理器单例。"""
    return ConnectionManager()


__all__ = ["AgentConnection", "ConnectionManager", "get_connection_manager"]
