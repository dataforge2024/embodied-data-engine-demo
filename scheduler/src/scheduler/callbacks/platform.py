"""回调 Platform（交互⑧）。

用专用凭据 ``X-Scheduler-Token``，不是用户 JWT —— 最小权限：Scheduler 只需要写回结果，
不需要能读用户数据。
"""

import logging

import httpx
from rdh_contract.schemas.scheduler import AlgoJobResult, AlgoResultCallback

logger = logging.getLogger(__name__)


class PlatformCallbackError(RuntimeError):
    """回调 Platform 失败。"""


class PlatformClient:
    """Platform HTTP 客户端。"""

    def __init__(self, *, base_url: str, scheduler_token: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = scheduler_token
        self._timeout = timeout_seconds

    async def report_algo_result(
        self,
        *,
        episode_id: str,
        results: tuple[AlgoJobResult, ...],
        pipeline_complete: bool,
    ) -> dict[str, object]:
        """上报算子结果（交互⑧）。

        ``pipeline_complete=False`` 时 Platform 只落数据；为 True 才推进 Episode 状态。
        409 视为**成功**：说明 Platform 侧状态已推进（重放），不该再重试。
        """
        from datetime import UTC, datetime

        callback = AlgoResultCallback(
            episode_id=episode_id,
            results=results,
            pipeline_complete=pipeline_complete,
            reported_at=datetime.now(UTC),
        )

        url = f"{self._base_url}/callbacks/algo-result"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    url,
                    json=callback.model_dump(mode="json"),
                    headers={"X-Scheduler-Token": self._token},
                )
            except httpx.HTTPError as exc:
                raise PlatformCallbackError(f"回调请求失败：{exc}") from exc

        if response.status_code == 409:
            logger.info("Platform 侧状态已推进（重放），视为成功 episode=%s", episode_id)
            return {"conflict": True}

        if response.status_code >= 400:
            raise PlatformCallbackError(f"回调返回 {response.status_code}：{response.text[:300]}")

        payload: dict[str, object] = response.json()
        return payload

    async def health(self) -> bool:
        """探测 Platform 是否可用。"""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(f"{self._base_url}/health")
            except httpx.HTTPError:
                return False
        return response.status_code == 200


__all__ = ["PlatformCallbackError", "PlatformClient"]
