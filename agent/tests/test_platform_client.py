"""PlatformClient 的状态码契约。

Platform 各端点声明的成功码不统一（``POST /episodes`` 是 201，其余是 200），
客户端一旦写错就整条链路断掉 —— 这里把每个端点接受的码钉住。

用 httpx 的 MockTransport 注入响应，不起真服务。
"""

from __future__ import annotations

import httpx
import pytest

from agent.platform_client import PlatformClient, PlatformError

BASE_URL = "http://platform.test/api/v1"
EPISODE_ID = "6104ab74-1eab-459f-9e40-e7b3b868f4c9"


def _client(handler: object, *, access_token: str | None = "jwt-token") -> PlatformClient:
    """构造客户端，并把它内部的 AsyncClient 换成 MockTransport 版本。

    PlatformClient 每次调用都新建 AsyncClient，因此这里 patch 类本身的构造，
    让它带上 mock transport。
    """
    original = httpx.AsyncClient

    def factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    httpx.AsyncClient = factory  # type: ignore[misc, assignment]
    return PlatformClient(
        base_url=BASE_URL,
        agent_token="local-agent-token",
        timeout_seconds=5.0,
        access_token=access_token,
    )


@pytest.fixture(autouse=True)
def restore_httpx() -> object:
    """每个用例后还原 httpx.AsyncClient，避免污染其他测试。"""
    original = httpx.AsyncClient
    yield
    httpx.AsyncClient = original  # type: ignore[misc, assignment]


class TestCreateEpisode:
    """``POST /episodes`` —— Platform 声明的是 201。"""

    async def test_accepts_201(self) -> None:
        """201 是正常路径。早先客户端断言 !=200 就报错，外部落地的文件全失败。"""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/episodes")
            return httpx.Response(201, json={"success": True, "data": {"episode_id": EPISODE_ID}})

        client = _client(handler)
        assert await _create(client) == EPISODE_ID

    async def test_accepts_200(self) -> None:
        """200 也接受 —— 不锁死在某一个码上。"""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "data": {"episode_id": EPISODE_ID}})

        assert await _create(_client(handler)) == EPISODE_ID

    async def test_rejects_error_status(self) -> None:
        """真正的失败仍要抛。"""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"success": False, "error": {"message": "校验失败"}})

        with pytest.raises(PlatformError, match="创建 Episode 失败 422"):
            await _create(_client(handler))


class TestStartUpload:
    """``POST /episodes/{id}/start-upload`` —— 200，409 视为重放。"""

    async def test_accepts_200(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "data": {}})

        await _client(handler).start_upload(EPISODE_ID)  # 不抛即通过

    async def test_conflict_is_replay_not_error(self) -> None:
        """409 表示已在上传态，恢复流程重放时必须放过。"""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"success": False, "error": {"message": "状态冲突"}})

        await _client(handler).start_upload(EPISODE_ID)

    async def test_rejects_error_status(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with pytest.raises(PlatformError, match="进入上传态失败 500"):
            await _client(handler).start_upload(EPISODE_ID)


class TestAuth:
    """凭据缺失要早失败，而不是发出一个没带头的请求。"""

    async def test_missing_access_token(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
            raise AssertionError("缺 token 时不该发请求")

        with pytest.raises(PlatformError, match="缺少 access token"):
            await _create(_client(handler, access_token=None))


class TestTokenExpiry:
    """JWT 过期后自动重登。

    回归测试：常驻 Agent 启动时登录一次，JWT 默认 1 小时过期。早先客户端握着过期 token
    不放，跑过一个 TTL 之后 ``POST /episodes`` 收到 401 直接放弃，文件全进 ``.failed/`` ——
    Agent 看着还在跑，实际再也传不上任何东西。
    """

    async def test_relogin_on_401_then_retry(self) -> None:
        """401 后重新登录并重试，最终成功。"""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append(path)
            if path.endswith("/auth/login"):
                return httpx.Response(
                    200, json={"success": True, "data": {"access_token": "fresh-token"}}
                )
            # 第一次带旧 token 来的请求返回 401，重登后再来才放行
            auth = request.headers.get("Authorization", "")
            if auth == "Bearer stale-token":
                return httpx.Response(
                    401, json={"success": False, "error": {"message": "token 已过期"}}
                )
            assert auth == "Bearer fresh-token"
            return httpx.Response(201, json={"success": True, "data": {"episode_id": EPISODE_ID}})

        client = _client(handler, access_token="stale-token").with_access_token(
            "stale-token", credentials=("admin", "pw")
        )
        assert await _create(client) == EPISODE_ID
        assert any(p.endswith("/auth/login") for p in calls), "应触发重新登录"

    async def test_refreshed_token_is_reused(self) -> None:
        """刷新后的 token 就地生效 —— 下一次调用不该再撞 401。

        FileProcessor 持有的是同一个客户端实例，刷新必须改到实例上；
        若返回新对象，调用方手里永远是过期那个。
        """
        logins = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal logins
            if request.url.path.endswith("/auth/login"):
                logins += 1
                return httpx.Response(
                    200, json={"success": True, "data": {"access_token": "fresh-token"}}
                )
            if request.headers.get("Authorization") == "Bearer stale-token":
                return httpx.Response(401, json={"success": False, "error": {"message": "过期"}})
            return httpx.Response(201, json={"success": True, "data": {"episode_id": EPISODE_ID}})

        client = _client(handler, access_token="stale-token").with_access_token(
            "stale-token", credentials=("admin", "pw")
        )
        await _create(client)
        await _create(client)
        assert logins == 1, f"token 应只刷新一次，实际登录 {logins} 次"

    async def test_start_upload_also_refreshes(self) -> None:
        """``start-upload`` 同样走用户 JWT，也要能自愈。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(
                    200, json={"success": True, "data": {"access_token": "fresh-token"}}
                )
            if request.headers.get("Authorization") == "Bearer stale-token":
                return httpx.Response(401, json={"success": False, "error": {"message": "过期"}})
            return httpx.Response(200, json={"success": True, "data": {}})

        client = _client(handler, access_token="stale-token").with_access_token(
            "stale-token", credentials=("admin", "pw")
        )
        await client.start_upload(EPISODE_ID)  # 不抛即通过

    async def test_without_credentials_gives_up(self) -> None:
        """没带凭据时不能假装成功 —— 401 照样抛，只是日志说明无法重登。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/auth/login"):  # pragma: no cover - 不该走到
                raise AssertionError("没有凭据时不该尝试登录")
            return httpx.Response(401, json={"success": False, "error": {"message": "过期"}})

        with pytest.raises(PlatformError, match="创建 Episode 失败 401"):
            await _create(_client(handler, access_token="stale-token"))

    async def test_second_401_is_not_retried_forever(self) -> None:
        """重登后仍 401 说明凭据本身不对，只重试一次就放弃。"""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(
                    200, json={"success": True, "data": {"access_token": "still-bad"}}
                )
            attempts += 1
            return httpx.Response(401, json={"success": False, "error": {"message": "过期"}})

        client = _client(handler, access_token="stale-token").with_access_token(
            "stale-token", credentials=("admin", "pw")
        )
        with pytest.raises(PlatformError, match="创建 Episode 失败 401"):
            await _create(client)
        assert attempts == 2, f"应只重试一次（共 2 次请求），实际 {attempts} 次"


async def _create(client: PlatformClient) -> str:
    return await client.create_episode(
        task_id="task-1",
        agent_id="agent-local-01",
        local_path="/tmp/ep.mcap",
        robot_model="rm-75-6f",
        scene="kitchen",
    )
