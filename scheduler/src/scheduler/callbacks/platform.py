"""回调 Platform（交互⑧）。

用专用凭据 ``X-Scheduler-Token``，不是用户 JWT —— 最小权限：Scheduler 只需要写回结果，
不需要能读用户数据。
"""

import logging

import httpx
from rdh_contract.schemas.base import ContractModel
from rdh_contract.schemas.scheduler import (
    AlgoJobResult,
    AlgoResultCallback,
    AnnotationProcessingCallback,
    DatasetBuildCallback,
)

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
        return await self._post("/callbacks/algo-result", callback, subject=episode_id)

    async def trigger_dataset_build(self, *, dataset_id: str) -> dict[str, object]:
        """触发训练集构建。

        构建本身在 Platform 做 —— 清单要写人工标注后的最终分段，那份数据只在 Platform
        的库里，而 Scheduler 按依赖铁律不能直连 DB。这里负责的是「什么时候建」
        （消费事件、重试），不是「建出什么」。
        """
        from datetime import UTC, datetime

        callback = DatasetBuildCallback(
            dataset_id=dataset_id, requested_at=datetime.now(UTC)
        )
        return await self._post("/callbacks/dataset-build", callback, subject=dataset_id)

    async def report_annotation_processing(
        self,
        *,
        episode_id: str,
        succeeded: bool,
        error_message: str | None = None,
    ) -> dict[str, object]:
        """上报送标处理结果。

        驱动 ``annotation_processing → annotation_pending``（成功）或 ``→ failed``。
        与 :meth:`report_algo_result` 是**两个不同端点**：后者的源状态是 ``processing``。
        """
        from datetime import UTC, datetime

        callback = AnnotationProcessingCallback(
            episode_id=episode_id,
            succeeded=succeeded,
            error_message=error_message,
            reported_at=datetime.now(UTC),
        )
        return await self._post(
            "/callbacks/annotation-processing", callback, subject=episode_id
        )

    async def _post(
        self, path: str, callback: ContractModel, *, subject: str
    ) -> dict[str, object]:
        """POST 一个回调 payload，统一处理重放与错误。

        409 视为**成功**：说明 Platform 侧状态已推进（重放），不该再重试。
        """
        url = f"{self._base_url}{path}"
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
            logger.info("Platform 侧状态已推进（重放），视为成功 subject=%s", subject)
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
