"""Agent 节点路由与 WebSocket 入口（交互①）。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, WebSocket
from fastapi.websockets import WebSocketDisconnect
from rdh_contract.enums import Role
from rdh_contract.schemas import AgentNode, ApiResponse, CollectTask
from rdh_contract.ws import ConsoleAgentStatusFrame

from app.api.dependencies import (
    AgentRepoDep,
    EpisodeRepoDep,
    PublisherDep,
    SessionDep,
    SettingsDep,
    TaskRepoDep,
    require_agent_token,
    require_roles,
)
from app.core.security import AuthError, decode_access_token
from app.ws.handlers import handle_agent_socket
from app.ws.manager import get_connection_manager

router = APIRouter(tags=["agents"])


@router.get("/agents", summary="Agent 节点列表")
async def list_agents(
    agents: AgentRepoDep,
    user: Annotated[object, Depends(require_roles(Role.ADMIN, Role.RECORDER))],
) -> ApiResponse[list[AgentNode]]:
    """SysOps 工作区：Agent 节点与在线状态（由心跳超时判定）。"""
    return ApiResponse(success=True, data=list(await agents.find_all()))


@router.get("/agents/online", summary="当前 WS 在线的 Agent")
async def online_agents(
    user: Annotated[object, Depends(require_roles(Role.ADMIN, Role.RECORDER))],
) -> ApiResponse[list[str]]:
    """进程内活跃 WS 连接。与 ``/agents`` 的 ``online`` 字段互为印证。"""
    return ApiResponse(success=True, data=list(get_connection_manager().online_agents()))


@router.get(
    "/agents/me/tasks",
    summary="Agent 拉取已分派任务",
    dependencies=[Depends(require_agent_token)],
)
async def get_my_tasks(
    x_agent_id: Annotated[str, Header()],
    tasks: TaskRepoDep,
) -> ApiResponse[list[CollectTask]]:
    """Agent 启动或重连时拉取（交互① 补发）。

    鉴权：``X-Agent-Token`` 验证身份；``X-Agent-ID`` 指定拉哪个 Agent 的任务。
    """
    assigned = await tasks.find_assigned_to(x_agent_id)
    return ApiResponse(success=True, data=assigned)


@router.websocket("/ws/console")
async def console_socket(socket: WebSocket, settings: SettingsDep, token: str = "") -> None:
    """浏览器控制台 WebSocket —— 推送 Agent 上下线与上传进度。

    鉴权走 query 参数 ``?token=<JWT>``：浏览器 ``new WebSocket()`` 没法带自定义
    header，这是标准做法。仅 admin 与 recorder 可连。

    单向推送。浏览器发来的任何帧都忽略 —— 操作走 REST，WS 只做通知。
    """
    manager = get_connection_manager()
    try:
        payload = decode_access_token(token, secret=settings.jwt_secret)
    except AuthError:
        await socket.close(code=4401, reason="token 无效")
        return

    allowed = {Role.ADMIN.value, Role.RECORDER.value}
    if not allowed & set(payload.roles):
        await socket.close(code=4403, reason="角色无权订阅")
        return

    await socket.accept()
    manager.add_console(socket)
    try:
        # 连上先给一次当前在线快照，避免页面要等下一次状态变化才有数据
        for agent_id in manager.online_agents():
            await socket.send_text(
                ConsoleAgentStatusFrame(
                    agent_id=agent_id, online=True, hostname=None, at=datetime.now(UTC)
                ).model_dump_json()
            )
        while True:
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_console(socket)


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
