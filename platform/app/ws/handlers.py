"""WebSocket 消息处理（交互①）。

上行帧用 contract 的判别式联合解析：类型不对、方向不对、字段缺失都在这里被拒，
不会流进业务逻辑。

**Agent 的状态上报不是权威**：``up.episode_status`` 仍要过 ``episode_lifecycle`` 守卫。
Agent 断电恢复时可能重放旧状态。
"""

import logging

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from rdh_contract.state_machine import InvalidTransitionError
from rdh_contract.ws import (
    UPSTREAM_ADAPTER,
    WS_PROTOCOL_VERSION,
    AckFrame,
    EpisodeStatusFrame,
    ErrorFrame,
    HeartbeatFrame,
    RegisterFrame,
    UploadProgressFrame,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent_node import AgentNodeRepository
from app.repositories.episode import EpisodeRepository
from app.services.episode_lifecycle import EpisodeLifecycleService, EpisodeNotFoundError
from app.services.event_publisher import EventPublisher
from app.services.progress_tracker import ProgressThrottle
from app.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)


async def _send_error(socket: WebSocket, *, code: str, message: str, fatal: bool = False) -> None:
    """回一条下行错误帧。"""
    import uuid

    frame = ErrorFrame(message_id=str(uuid.uuid4()), code=code, message=message, fatal=fatal)
    await socket.send_text(frame.model_dump_json())


async def handle_agent_socket(
    socket: WebSocket,
    *,
    manager: ConnectionManager,
    session: AsyncSession,
    agents: AgentNodeRepository,
    episodes: EpisodeRepository,
    publisher: EventPublisher,
    heartbeat_timeout_seconds: int,
) -> None:
    """处理一条 Agent 连接的完整生命周期。

    协议要求第一帧必须是 ``up.register``，否则直接拒绝 —— 未注册的连接不接受业务消息。
    """
    await socket.accept()
    agent_id: str | None = None
    lifecycle = EpisodeLifecycleService(episodes=episodes, publisher=publisher)
    throttle = ProgressThrottle()

    try:
        # ---- 第一帧必须是注册 ----
        raw = await socket.receive_text()
        try:
            first = UPSTREAM_ADAPTER.validate_json(raw)
        except ValidationError:
            await _send_error(socket, code="INVALID_FRAME", message="首帧解析失败", fatal=True)
            await socket.close()
            return

        if not isinstance(first, RegisterFrame):
            await _send_error(
                socket, code="EXPECT_REGISTER", message="首帧必须是 up.register", fatal=True
            )
            await socket.close()
            return

        major = first.protocol_version.split(".", 1)[0]
        if major != WS_PROTOCOL_VERSION.split(".", 1)[0]:
            await _send_error(
                socket,
                code="PROTOCOL_MISMATCH",
                message=f"协议主版本不兼容：服务端 {WS_PROTOCOL_VERSION}",
                fatal=True,
            )
            await socket.close()
            return

        agent_id = first.agent_id
        await agents.register(agent_id=agent_id, hostname=first.hostname, version=first.version)
        await session.commit()
        await manager.register(agent_id, socket)
        logger.info("Agent 已注册 agent_id=%s host=%s", agent_id, first.hostname)

        # ---- 消息循环 ----
        while True:
            raw = await socket.receive_text()
            try:
                frame = UPSTREAM_ADAPTER.validate_json(raw)
            except ValidationError:
                await _send_error(socket, code="INVALID_FRAME", message="帧解析失败")
                continue

            if isinstance(frame, HeartbeatFrame):
                manager.touch(frame.payload.agent_id)
                await agents.record_heartbeat(frame.payload)
                await session.commit()

            elif isinstance(frame, EpisodeStatusFrame):
                # Agent 的上报不是权威，仍过守卫
                try:
                    await lifecycle.transition(frame.episode_id, target=frame.status)
                    await session.commit()
                except InvalidTransitionError as exc:
                    await session.rollback()
                    await _send_error(socket, code="INVALID_STATE_TRANSITION", message=str(exc))
                except EpisodeNotFoundError:
                    await session.rollback()
                    await _send_error(socket, code="EPISODE_NOT_FOUND", message="Episode 不存在")

            elif isinstance(frame, UploadProgressFrame):
                # 节流落库：2秒 或 5% 或 末片
                if throttle.should_write(
                    frame.episode_id,
                    uploaded_parts=frame.uploaded_parts,
                    total_parts=frame.total_parts,
                ):
                    try:
                        await episodes.update_upload_progress(
                            frame.episode_id,
                            uploaded_parts=frame.uploaded_parts,
                            total_parts=frame.total_parts,
                        )
                        await session.commit()
                        logger.debug(
                            "进度已落库 episode=%s %d/%d",
                            frame.episode_id,
                            frame.uploaded_parts,
                            frame.total_parts,
                        )
                        # 转发给浏览器控制台
                        await manager.notify_upload_progress(
                            episode_id=frame.episode_id,
                            agent_id=agent_id,
                            uploaded_parts=frame.uploaded_parts,
                            total_parts=frame.total_parts,
                        )
                    except KeyError:
                        await session.rollback()
                        await _send_error(
                            socket, code="EPISODE_NOT_FOUND", message="Episode 不存在"
                        )
                    except Exception:
                        await session.rollback()
                        logger.exception("进度落库失败 episode=%s", frame.episode_id)
                else:
                    logger.debug(
                        "进度节流跳过 episode=%s %d/%d",
                        frame.episode_id,
                        frame.uploaded_parts,
                        frame.total_parts,
                    )

            elif isinstance(frame, AckFrame):
                manager.ack(agent_id, frame.message_id)

            elif isinstance(frame, RegisterFrame):
                await _send_error(socket, code="ALREADY_REGISTERED", message="重复注册")

    except WebSocketDisconnect:
        logger.info("Agent 断开 agent_id=%s", agent_id)
    except Exception:
        logger.exception("WS 处理异常 agent_id=%s", agent_id)
        await session.rollback()
    finally:
        if agent_id is not None:
            await manager.disconnect(agent_id)


__all__ = ["handle_agent_socket"]
