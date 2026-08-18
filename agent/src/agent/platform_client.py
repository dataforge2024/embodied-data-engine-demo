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
    """Platform HTTP 客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        agent_token: str,
        timeout_seconds: float,
        access_token: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._agent_token = agent_token
        self._timeout = timeout_seconds
        self._access_token = access_token

    def with_access_token(self, token: str) -> "PlatformClient":
        """返回带用户 JWT 的新客户端（不可变更新）。"""
        return PlatformClient(
            base_url=self._base_url,
            agent_token=self._agent_token,
            timeout_seconds=self._timeout,
            access_token=token,
        )

    def _auth_headers(self) -> dict[str, str]:
        """用户 JWT 头。"""
        if not self._access_token:
            raise PlatformError("缺少 access token")
        return {"Authorization": f"Bearer {self._access_token}"}

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/episodes",
                json={
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "local_path": local_path,
                    "robot_model": robot_model,
                    "scene": scene,
                },
                headers=self._auth_headers(),
            )
        if response.status_code != 200:
            raise PlatformError(f"创建 Episode 失败 {response.status_code}：{response.text[:200]}")
        episode_id: str = response.json()["data"]["episode_id"]
        return episode_id

    async def start_upload(self, episode_id: str) -> None:
        """``recording → uploading``。"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/episodes/{episode_id}/start-upload",
                headers=self._auth_headers(),
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

    async def health(self) -> bool:
        """探测 Platform 可用性。"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(f"{self._base_url}/health")
            except httpx.HTTPError:
                return False
        return response.status_code == 200


__all__ = ["PlatformClient", "PlatformError"]
