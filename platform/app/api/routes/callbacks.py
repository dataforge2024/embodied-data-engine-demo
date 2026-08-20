"""内部回调路由。

**两个独立端点，不要合并** —— 调用方、凭据、语义、驱动的状态迁移都不同：

``/callbacks/upload-complete``
    调用方 Agent，凭据 ``X-Agent-Token``，驱动 ``uploading → uploaded``
``/callbacks/algo-result``
    调用方 Scheduler，凭据 ``X-Scheduler-Token``，
    驱动 ``processing → verification_pending`` 或 ``→ failed``
``/callbacks/annotation-processing``
    调用方 Scheduler，凭据 ``X-Scheduler-Token``，
    驱动 ``annotation_processing → annotation_pending`` 或 ``→ failed``
"""

from fastapi import APIRouter, Depends
from rdh_contract.schemas import ApiResponse, Episode
from rdh_contract.schemas.agent import UploadCallback
from rdh_contract.schemas.scheduler import AlgoResultCallback, AnnotationProcessingCallback

from app.api.dependencies import (
    CallbackServiceDep,
    SessionDep,
    require_agent_token,
    require_scheduler_token,
)

router = APIRouter(prefix="/callbacks", tags=["callbacks"])


@router.post(
    "/upload-complete",
    summary="上传完成回调（交互③，Agent 调用）",
    dependencies=[Depends(require_agent_token)],
)
async def upload_complete(
    callback: UploadCallback,
    service: CallbackServiceDep,
    session: SessionDep,
) -> ApiResponse[Episode]:
    """Agent 分片上传结束后调用。

    服务端独立重算 checksum；校验通过后驱动 ``uploading → uploaded``
    并发布 ``episode.uploaded``（交互⑤）。
    """
    outcome = await service.handle_upload_complete(callback)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


@router.post(
    "/algo-result",
    summary="算子结果回调（交互⑧，Scheduler 调用）",
    dependencies=[Depends(require_scheduler_token)],
)
async def algo_result(
    callback: AlgoResultCallback,
    service: CallbackServiceDep,
    session: SessionDep,
) -> ApiResponse[Episode]:
    """Scheduler 汇报算子结果。

    ``pipeline_complete=false`` 时只落数据；为 true 时才推进 Episode 状态。
    """
    outcome = await service.handle_algo_result(callback)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


@router.post(
    "/annotation-processing",
    summary="送标处理结果回调（Scheduler 调用）",
    dependencies=[Depends(require_scheduler_token)],
)
async def annotation_processing(
    callback: AnnotationProcessingCallback,
    service: CallbackServiceDep,
    session: SessionDep,
) -> ApiResponse[Episode]:
    """Scheduler 汇报送标处理结果。

    驱动 ``annotation_processing → annotation_pending``（成功）或 ``→ failed``（失败）。
    与 ``/callbacks/algo-result`` 的区别是源状态不同，见 callbacks 服务的 docstring。
    """
    outcome = await service.handle_annotation_processing(callback)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


__all__ = ["router"]
