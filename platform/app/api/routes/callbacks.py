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
``/callbacks/dataset-build``
    调用方 Scheduler，凭据 ``X-Scheduler-Token``，
    驱动 dataset ``pending → running → succeeded`` 或 ``→ failed``
"""

from fastapi import APIRouter, Depends, HTTPException
from rdh_contract.schemas import ApiResponse, Dataset, Episode
from rdh_contract.schemas.agent import UploadCallback
from rdh_contract.schemas.scheduler import (
    AlgoResultCallback,
    AnnotationProcessingCallback,
    DatasetBuildCallback,
)

from app.api.dependencies import (
    CallbackServiceDep,
    DatasetBuilderDep,
    SessionDep,
    require_agent_token,
    require_scheduler_token,
)
from app.services.dataset_builder import DatasetBuildError

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


@router.post(
    "/dataset-build",
    summary="触发训练集构建（Scheduler 调用）",
    dependencies=[Depends(require_scheduler_token)],
)
async def dataset_build(
    callback: DatasetBuildCallback,
    builder: DatasetBuilderDep,
    session: SessionDep,
) -> ApiResponse[Dataset]:
    """Scheduler 收到 ``dataset.build_requested`` 后调本端点做实际构建。

    构建放在 Platform 而不是 Scheduler：清单要写人工标注后的最终分段，那份数据只在
    Platform 的库里，而 Scheduler 按依赖铁律不能直连 DB。Scheduler 负责的是「什么时候
    构建」（消费事件、重试），Platform 负责「构建出什么」。

    失败时 dataset 已落 ``failed``，所以这里回 422 让 Scheduler 别再重试 ——
    清单缺 Episode 这类问题重试多少次都一样。
    """
    try:
        dataset = await builder.build(callback.dataset_id)
    except DatasetBuildError as exc:
        await session.commit()  # 失败状态要落库，否则查到的一直是 running
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return ApiResponse(success=True, data=dataset)


__all__ = ["router"]
