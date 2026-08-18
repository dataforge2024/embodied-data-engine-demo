"""Agent 节点路由与 WebSocket 入口（交互①）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket
from rdh_contract.enums import Role
from rdh_contract.schemas import AgentNode, ApiResponse

from app.api.dependencies import (
    AgentRepoDep,
    EpisodeRepoDep,
    PublisherDep,
    SessionDep,
    SettingsDep,
    require_roles,
)
from app.ws.handlers import handle_agent_socket
from app.ws.manager import get_connection_manager

router = APIRouter(tags=["agents"])


@router.get("/agents", summary="Agent 节点列表")
async def list_agents(
    agents: AgentRepoDep,
    user: Annotated[object, Depends(require_roles(Role.SYSOPS))],
) -> ApiResponse[list[AgentNode]]:
    """SysOps 工作区：Agent 节点与在线状态（由心跳超时判定）。"""
    return ApiResponse(success=True, data=list(await agents.find_all()))


@router.get("/agents/online", summary="当前 WS 在线的 Agent")
async def online_agents(
    user: Annotated[object, Depends(require_roles(Role.SYSOPS))],
) -> ApiResponse[list[str]]:
    """进程内活跃 WS 连接。与 ``/agents`` 的 ``online`` 字段互为印证。"""
    return ApiResponse(success=True, data=list(get_connection_manager().online_agents()))


@router.websocket("/ws/agent")
async def agent_socket(
    socket: WebSocket,
    session: SessionDep,
    agents: AgentRepoDep,
    episodes: EpisodeRepoDep,
    publisher: PublisherDep,
    settings: SettingsDep,
) -> None:
    """Agent WebSocket 端点（交互①）。

    首帧必须是 ``up.register``。鉴权在注册帧内校验 —— WS 握手阶段带不了自定义 header。
    """
    await handle_agent_socket(
        socket,
        manager=get_connection_manager(),
        session=session,
        agents=agents,
        episodes=episodes,
        publisher=publisher,
        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
    )


__all__ = ["router"]
