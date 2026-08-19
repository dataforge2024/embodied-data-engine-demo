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


async def _create(client: PlatformClient) -> str:
    return await client.create_episode(
        task_id="task-1",
        agent_id="agent-local-01",
        local_path="/tmp/ep.mcap",
        robot_model="rm-75-6f",
        scene="kitchen",
    )
