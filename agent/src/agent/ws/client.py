"""WebSocket 客户端（交互①）。

三件事必须做对：

1. **首帧注册** —— 协议要求，未注册的连接不被 Platform 接受
2. **心跳** —— 按 contract 声明的间隔发，Platform 超时判离线
3. **指数退避重连** —— 采集 PC 网络不稳定；固定间隔重连会在断网时压垮 Platform
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import websockets
from rdh_contract.enums import EpisodeStatus
from rdh_contract.schemas.agent import AgentHeartbeat
from rdh_contract.ws import (
    DOWNSTREAM_ADAPTER,
    HEARTBEAT_INTERVAL_SECONDS,
    WS_PROTOCOL_VERSION,
    AckFrame,
    DownstreamFrame,
    EpisodeStatusFrame,
    HeartbeatFrame,
    RegisterFrame,
    TaskPushFrame,
    UploadProgressFrame,
)

logger = logging.getLogger(__name__)

FrameHandler = Callable[[DownstreamFrame], Awaitable[None]]


class AgentSocket:
    """Agent 的 WS 连接。

    只封装「连上、注册、收发、重连」，业务处理由 ``on_frame`` 回调决定。
    """

    def __init__(
        self,
        *,
        url: str,
        agent_id: str,
        hostname: str,
        version: str,
        reconnect_initial_seconds: float = 1.0,
        reconnect_max_seconds: float = 30.0,
    ) -> None:
        self._url = url
        self._agent_id = agent_id
        self._hostname = hostname
        self._version = version
        self._initial_backoff = reconnect_initial_seconds
        self._max_backoff = reconnect_max_seconds
        self._connection: websockets.ClientConnection | None = None

    @property
    def connected(self) -> bool:
        """当前是否已连接。"""
        return self._connection is not None

    async def connect(self) -> None:
        """建立连接并发送注册帧。"""
        self._connection = await websockets.connect(self._url)
        await self._send(
            RegisterFrame(
                agent_id=self._agent_id,
                hostname=self._hostname,
                version=self._version,
                protocol_version=WS_PROTOCOL_VERSION,
            )
        )
        # 等 down.registered
        raw = await self._connection.recv()
        frame = DOWNSTREAM_ADAPTER.validate_json(raw)
        logger.info("WS 已注册 agent_id=%s 服务端回 %s", self._agent_id, type(frame).__name__)

    async def close(self) -> None:
        """关闭连接。"""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def send_heartbeat(
        self,
        *,
        pending_upload_count: int,
        disk_free_bytes: int,
        recording_episode_id: str | None = None,
    ) -> None:
        """发送心跳。"""
        await self._send(
            HeartbeatFrame(
                payload=AgentHeartbeat(
                    agent_id=self._agent_id,
                    version=self._version,
                    reported_at=datetime.now(UTC),
                    recording_episode_id=recording_episode_id,
                    pending_upload_count=pending_upload_count,
                    disk_free_bytes=disk_free_bytes,
                )
            )
        )

    async def report_status(
        self, episode_id: str, status: EpisodeStatus, *, detail: str | None = None
    ) -> None:
        """上报 Episode 状态。

        **这只是观察上报，不是权威决定** —— Platform 仍会过状态机守卫。
        """
        await self._send(
            EpisodeStatusFrame(
                episode_id=episode_id,
                status=status,
                reported_at=datetime.now(UTC),
                detail=detail,
            )
        )

    async def report_upload_progress(
        self, episode_id: str, *, uploaded_parts: int, total_parts: int
    ) -> None:
        """上报上传进度（供 SysOps 观察）。"""
        await self._send(
            UploadProgressFrame(
                episode_id=episode_id,
                uploaded_parts=uploaded_parts,
                total_parts=total_parts,
            )
        )

    async def ack(self, message_id: str) -> None:
        """确认收到下行消息。"""
        await self._send(AckFrame(message_id=message_id))

    async def receive(self, timeout: float | None = None) -> DownstreamFrame | None:
        """收一帧。超时或连接关闭返回 None。"""
        if self._connection is None:
            return None
        try:
            raw = await asyncio.wait_for(self._connection.recv(), timeout=timeout)
        except (TimeoutError, websockets.ConnectionClosed):
            return None
        try:
            return DOWNSTREAM_ADAPTER.validate_json(raw)
        except Exception:
            logger.warning("下行帧解析失败：%s", str(raw)[:200])
            return None

    async def run_with_reconnect(
        self,
        *,
        on_frame: FrameHandler,
        heartbeat_provider: Callable[[], tuple[int, int, str | None]],
        stop_event: asyncio.Event,
    ) -> None:
        """常驻运行：自动重连 + 定期心跳。

        退避从 ``reconnect_initial_seconds`` 翻倍到 ``reconnect_max_seconds``；
        连接成功后重置 —— 否则一次长时间断网会让后续重连一直是最大间隔。
        """
        backoff = self._initial_backoff
        while not stop_event.is_set():
            try:
                await self.connect()
                backoff = self._initial_backoff  # 成功即重置
                await self._session_loop(
                    on_frame=on_frame,
                    heartbeat_provider=heartbeat_provider,
                    stop_event=stop_event,
                )
            except (OSError, websockets.WebSocketException) as exc:
                logger.warning("WS 连接异常，%.1fs 后重连：%s", backoff, exc)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                backoff = min(backoff * 2, self._max_backoff)
            finally:
                await self.close()

    async def _session_loop(
        self,
        *,
        on_frame: FrameHandler,
        heartbeat_provider: Callable[[], tuple[int, int, str | None]],
        stop_event: asyncio.Event,
    ) -> None:
        """单次连接内的收发循环。"""
        last_heartbeat = 0.0
        while not stop_event.is_set():
            loop_time = asyncio.get_running_loop().time()
            if loop_time - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                pending, disk_free, recording = heartbeat_provider()
                await self.send_heartbeat(
                    pending_upload_count=pending,
                    disk_free_bytes=disk_free,
                    recording_episode_id=recording,
                )
                last_heartbeat = loop_time

            frame = await self.receive(timeout=1.0)
            if frame is None:
                continue
            if isinstance(frame, TaskPushFrame):
                await self.ack(frame.message_id)
            await on_frame(frame)

    async def _send(self, frame: object) -> None:
        """发送一帧。"""
        if self._connection is None:
            raise RuntimeError("WS 未连接")
        payload = frame.model_dump_json()  # type: ignore[attr-defined]
        await self._connection.send(payload)

    @staticmethod
    def describe(frame: DownstreamFrame) -> str:
        """帧的简短描述，日志用。"""
        return json.dumps({"type": getattr(frame, "type", "?")}, ensure_ascii=False)


__all__ = ["AgentSocket", "FrameHandler"]
