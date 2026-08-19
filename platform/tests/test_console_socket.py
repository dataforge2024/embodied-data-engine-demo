"""浏览器控制台 WebSocket 的推送链路。

demo.py 走的是 Agent 侧 WS，不碰 `/ws/console`，所以这里单独覆盖主流程：
鉴权拒绝、连上后的在线快照、广播能落到浏览器。

只挂 agents 路由、不跑 lifespan —— 本文件不需要 DB。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from rdh_contract.enums import Role
from starlette.websockets import WebSocketDisconnect

from app.api.routes import agents as agents_route
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.ws.manager import AgentConnection, ConnectionManager

pytestmark = pytest.mark.integration

# local- 前缀是仓库约定：非真实密钥，生产启动时会被 assert_production_ready 拒绝
SECRET = "local-test-only-jwt-secret"
CONSOLE_URL = "/api/v1/ws/console"


def _token(*roles: Role) -> str:
    return create_access_token(user_id="u-1", roles=roles, secret=SECRET, ttl_seconds=300)


class _FakeSocket:
    """够用的 WebSocket 替身：只记录发出去的文本。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, payload: str) -> None:
        if self._fail:
            raise RuntimeError("连接已断")
        self.sent.append(payload)


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> ConnectionManager:
    """每个用例一个干净的 manager，连接池不跨用例串味。"""
    fresh = ConnectionManager()
    monkeypatch.setattr(agents_route, "get_connection_manager", lambda: fresh)
    return fresh


@pytest.fixture
def client(manager: ConnectionManager) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(agents_route.router, prefix="/api/v1")
    app.dependency_overrides[get_settings] = lambda: Settings(jwt_secret=SECRET)
    with TestClient(app) as test_client:
        yield test_client


def _add_agent(manager: ConnectionManager, agent_id: str) -> None:
    """直接塞连接池 —— 这里只验证快照，不跑 Agent 注册流程。"""
    now = datetime.now(UTC)
    manager._connections[agent_id] = AgentConnection(  # noqa: SLF001
        agent_id=agent_id,
        socket=_FakeSocket(),  # type: ignore[arg-type]
        session_id="s-1",
        connected_at=now,
        last_seen=now,
    )


class TestConsoleAuth:
    """鉴权：token 与角色。"""

    def test_rejects_invalid_token(self, client: TestClient) -> None:
        """签名错的 token 连不上。"""
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(f"{CONSOLE_URL}?token=garbage") as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4401

    def test_rejects_missing_token(self, client: TestClient) -> None:
        """不带 token 也走同一条拒绝路径。"""
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(CONSOLE_URL) as ws,
        ):
            ws.receive_text()

    def test_rejects_role_without_permission(self, client: TestClient) -> None:
        """lab 角色不在允许集合里。"""
        with (
            pytest.raises(WebSocketDisconnect) as excinfo,
            client.websocket_connect(f"{CONSOLE_URL}?token={_token(Role.LAB)}") as ws,
        ):
            ws.receive_text()
        assert excinfo.value.code == 4403

    def test_recorder_may_subscribe(
        self, client: TestClient, manager: ConnectionManager
    ) -> None:
        """recorder 也要能订阅 —— 采集记录页的进度条依赖它。"""
        _add_agent(manager, "agent-a")
        with client.websocket_connect(f"{CONSOLE_URL}?token={_token(Role.RECORDER)}") as ws:
            frame = json.loads(ws.receive_text())
        assert frame["agent_id"] == "agent-a"


class TestConsoleSnapshot:
    """连上先给一次在线快照。"""

    def test_sends_online_snapshot(self, client: TestClient, manager: ConnectionManager) -> None:
        """否则页面要干等下一次状态变化才有数据。"""
        _add_agent(manager, "agent-a")
        with client.websocket_connect(f"{CONSOLE_URL}?token={_token(Role.ADMIN)}") as ws:
            frame = json.loads(ws.receive_text())

        assert frame["type"] == "console.agent_status"
        assert frame["agent_id"] == "agent-a"
        assert frame["online"] is True

    def test_console_removed_after_disconnect(
        self, client: TestClient, manager: ConnectionManager
    ) -> None:
        """关标签页后连接池要收干净，否则后续广播反复抛错。"""
        with client.websocket_connect(f"{CONSOLE_URL}?token={_token(Role.ADMIN)}"):
            assert manager.console_count == 1
        assert manager.console_count == 0


class TestBroadcast:
    """manager 侧的广播与百分比换算。"""

    async def test_progress_percent_derived_from_parts(self) -> None:
        """percent 由 Platform 从分片数算出，前端不再各算一遍。"""
        manager = ConnectionManager()
        socket = _FakeSocket()
        manager.add_console(socket)  # type: ignore[arg-type]

        await manager.notify_upload_progress(
            episode_id="e1", agent_id="a1", uploaded_parts=3, total_parts=8
        )

        frame = json.loads(socket.sent[0])
        assert frame["type"] == "console.upload_progress"
        assert frame["uploaded_parts"] == 3
        assert frame["total_parts"] == 8
        assert frame["percent"] == 37.5

    async def test_agent_status_broadcast(self) -> None:
        """上下线广播带上 agent_id 与在线态。"""
        manager = ConnectionManager()
        socket = _FakeSocket()
        manager.add_console(socket)  # type: ignore[arg-type]

        await manager.notify_agent_status("a1", online=False)

        frame = json.loads(socket.sent[0])
        assert frame["type"] == "console.agent_status"
        assert frame["agent_id"] == "a1"
        assert frame["online"] is False

    async def test_dead_console_dropped(self) -> None:
        """发送失败的连接就地摘除，不影响其他浏览器收帧。"""
        manager = ConnectionManager()
        dead, alive = _FakeSocket(fail=True), _FakeSocket()
        manager.add_console(dead)  # type: ignore[arg-type]
        manager.add_console(alive)  # type: ignore[arg-type]

        await manager.notify_agent_status("a1", online=True)

        assert manager.console_count == 1
        assert len(alive.sent) == 1
