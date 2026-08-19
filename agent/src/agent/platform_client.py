"""Platform HTTP 客户端（交互③）。

上传完成回调用 Agent 专用凭据 ``X-Agent-Token``，其余端点用用户 JWT。
"""

import logging
from datetime import UTC, datetime

import httpx
from rdh_contract.schemas.agent import UploadCallback

logger = logging.getLogger(__name__)


class PlatformError(RuntimeError):
    """调用 Platform 失败。"""


class PlatformClient:
    """Platform HTTP 客户端。

    ``access_token`` 是**有意可变的缓存状态**：JWT 有有效期（默认 1 小时），而常驻 Agent
    要跑几天。持有本客户端的 :class:`~agent.file_processor.FileProcessor` 拿的是同一个实例，
    所以刷新必须就地生效，不能返回新对象 —— 否则调用方手里永远是那个过期 token。
    其余字段仍不可变。
    """

    def __init__(
        self,
        *,
        base_url: str,
        agent_token: str,
        timeout_seconds: float,
        access_token: str | None = None,
        credentials: tuple[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._agent_token = agent_token
        self._timeout = timeout_seconds
        self._access_token = access_token
        # 留着用于 token 过期后自动重登。为 None 时过期只能报错
        self._credentials = credentials

    def with_access_token(
        self, token: str, *, credentials: tuple[str, str] | None = None
    ) -> "PlatformClient":
        """返回带用户 JWT 的新客户端。

        传 ``credentials`` 才能在 token 过期后自动重登 —— 常驻模式必须传，
        否则跑过一个 TTL 之后所有用户 JWT 端点都会 401。
        """
        return PlatformClient(
            base_url=self._base_url,
            agent_token=self._agent_token,
            timeout_seconds=self._timeout,
            access_token=token,
            credentials=credentials if credentials is not None else self._credentials,
        )

    def _auth_headers(self) -> dict[str, str]:
        """用户 JWT 头。"""
        if not self._access_token:
            raise PlatformError("缺少 access token")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _relogin(self) -> bool:
        """token 过期后重新登录，成功返回 True。"""
        if self._credentials is None:
            logger.error("access token 失效，但客户端未持有凭据，无法自动重登")
            return False
        username, password = self._credentials
        try:
            self._access_token = await self.login(username, password)
        except PlatformError as exc:
            logger.error("自动重登失败：%s", exc)
            return False
        logger.info("access token 已失效，重新登录成功")
        return True

    async def _send_authed(
        self, method: str, path: str, *, json: dict | None = None
    ) -> httpx.Response:
        """带用户 JWT 发请求；401 时重新登录并重试一次。

        只重试一次：第二次还 401 说明不是过期而是凭据本身不对，重试到死没有意义。
        """
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method, url, json=json, headers=self._auth_headers()
            )
            if response.status_code != 401:
                return response
            if not await self._relogin():
                return response
            return await client.request(
                method, url, json=json, headers=self._auth_headers()
            )

    async def login(self, username: str, password: str) -> str:
        """登录取 JWT。"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/auth/login",
                json={"username": username, "password": password},
            )
        if response.status_code != 200:
            raise PlatformError(f"登录失败 {response.status_code}：{response.text[:200]}")
        token: str = response.json()["data"]["access_token"]
        return token

    async def create_episode(
        self, *, task_id: str, agent_id: str, local_path: str, robot_model: str, scene: str
    ) -> str:
        """登记 Episode（状态 ``recording``），返回 episode_id。"""
        response = await self._send_authed(
            "POST",
            "/episodes",
            json={
                "task_id": task_id,
                "agent_id": agent_id,
                "local_path": local_path,
                "robot_model": robot_model,
                "scene": scene,
            },
        )
        # Platform 的 POST /episodes 声明的是 201（见 routes/episodes.py），不是 200
        if response.status_code not in (200, 201):
            raise PlatformError(f"创建 Episode 失败 {response.status_code}：{response.text[:200]}")
        episode_id: str = response.json()["data"]["episode_id"]
        return episode_id

    async def start_upload(self, episode_id: str) -> None:
        """``recording → uploading``。"""
        response = await self._send_authed(
            "POST", f"/episodes/{episode_id}/start-upload"
        )
        if response.status_code == 409:
            logger.info("Episode 已在上传态（重放）episode=%s", episode_id)
            return
        if response.status_code != 200:
            raise PlatformError(f"进入上传态失败 {response.status_code}：{response.text[:200]}")

    async def report_upload_complete(
        self,
        *,
        episode_id: str,
        object_key: str,
        size_bytes: int,
        checksum: str,
        duration_ms: int,
        recorded_topics: tuple[str, ...],
    ) -> bool:
        """上传完成回调（交互③）。

        用 Agent 专用凭据，不是用户 JWT。409 视为成功 —— Platform 侧状态已推进（重放）。
        """
        callback = UploadCallback(
            episode_id=episode_id,
            object_key=object_key,
            size_bytes=size_bytes,
            checksum=checksum,
            duration_ms=duration_ms,
            recorded_topics=recorded_topics,
            completed_at=datetime.now(UTC),
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/callbacks/upload-complete",
                json=callback.model_dump(mode="json"),
                headers={"X-Agent-Token": self._agent_token},
            )
        if response.status_code == 409:
            logger.info("Platform 侧已处理该上传（重放）episode=%s", episode_id)
            return True
        if response.status_code != 200:
            raise PlatformError(f"上传回调失败 {response.status_code}：{response.text[:300]}")
        return True

    async def fetch_assigned_tasks(self, agent_id: str) -> list[dict]:
        """拉取分派给指定 Agent 的任务（交互① 补发，启动与重连时调）。

        用 Agent 专用凭据而非用户 JWT —— Agent 是 WS 注册的实体，没有用户身份。

        失败返回空列表而不抛异常 —— 拉不到任务不该阻止 Agent 启动，
        WS 重连后 Platform 还会推。
        """
        headers = {"X-Agent-Token": self._agent_token, "X-Agent-ID": agent_id}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(
                    f"{self._base_url}/agents/me/tasks", headers=headers
                )
            except httpx.HTTPError as exc:
                logger.warning("拉取已分派任务失败：%s", exc)
                return []
        if response.status_code != 200:
            logger.warning(
                "拉取已分派任务失败 %d：%s", response.status_code, response.text[:200]
            )
            return []
        tasks: list[dict] = response.json()["data"]
        return tasks

    async def health(self) -> bool:
        """探测 Platform 可用性。"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(f"{self._base_url}/health")
            except httpx.HTTPError:
                return False
        return response.status_code == 200


__all__ = ["PlatformClient", "PlatformError"]
