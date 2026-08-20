"""核验、标注、审核路由（交互④，Tool 调用）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from rdh_contract.enums import Role
from rdh_contract.schemas import (
    Annotation,
    AnnotationSubmit,
    ApiResponse,
    Episode,
    PageMeta,
    PaginatedResponse,
    ReviewResult,
    VerifyResult,
)

from app.api.dependencies import (
    AnnotationRepoDep,
    CurrentUserDep,
    ReviewServiceDep,
    SessionDep,
    require_roles,
)

verification_router = APIRouter(prefix="/verification", tags=["verification"])
annotation_router = APIRouter(prefix="/annotation", tags=["annotation"])


@verification_router.get("/queue", summary="核验队列")
async def verification_queue(
    service: ReviewServiceDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedResponse[Episode]:
    """待核验 Episode（状态 ``verification_pending``），FIFO。"""
    records, total = await service.verification_queue(page=page, limit=limit)
    return PaginatedResponse(
        success=True, data=list(records), meta=PageMeta(total=total, page=page, limit=limit)
    )


@verification_router.post("/{episode_id}", summary="提交核验结果")
async def submit_verification(
    episode_id: str,
    result: VerifyResult,
    service: ReviewServiceDep,
    session: SessionDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
) -> ApiResponse[Episode]:
    """通过 → ``annotation_pending``；打回 → ``rejected`` 终态并发事件。

    路径参数与请求体的 ``episode_id`` 不一致时报 422 —— 防止误操作改错 Episode。
    """
    if result.episode_id != episode_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="路径与请求体的 episode_id 不一致")
    outcome = await service.submit_verification(result)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


@annotation_router.get("/queue", summary="标注队列")
async def annotation_queue(
    service: ReviewServiceDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedResponse[Episode]:
    """待标注 Episode（状态 ``annotation_pending``），FIFO。"""
    records, total = await service.annotation_queue(page=page, limit=limit)
    return PaginatedResponse(
        success=True, data=list(records), meta=PageMeta(total=total, page=page, limit=limit)
    )


@annotation_router.get("/review-queue", summary="审核队列")
async def review_queue(
    service: ReviewServiceDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PaginatedResponse[Episode]:
    """待审核 Episode（状态 ``annotation_review``），FIFO。"""
    records, total = await service.review_queue(page=page, limit=limit)
    return PaginatedResponse(
        success=True, data=list(records), meta=PageMeta(total=total, page=page, limit=limit)
    )


@annotation_router.get("/{episode_id}", summary="获取标注详情")
async def get_annotation(
    episode_id: str, annotations: AnnotationRepoDep, user: CurrentUserDep
) -> ApiResponse[Annotation]:
    """查询标注记录（含核验与审核轨迹）。"""
    annotation = await annotations.find_by_episode(episode_id)
    if annotation is None:
        raise KeyError(episode_id)
    return ApiResponse(success=True, data=annotation)


@annotation_router.post("/{episode_id}", summary="提交标注")
async def submit_annotation(
    episode_id: str,
    submission: AnnotationSubmit,
    service: ReviewServiceDep,
    session: SessionDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
) -> ApiResponse[Annotation]:
    """提交标注。``segments`` 为全量分段（非增量），提交后进 ``annotation_review``。"""
    if submission.episode_id != episode_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="路径与请求体的 episode_id 不一致")

    from rdh_contract.schemas import User as ContractUser

    annotated_by = user.user_id if isinstance(user, ContractUser) else "unknown"
    annotation, _ = await service.submit_annotation(submission, annotated_by=annotated_by)
    await session.commit()
    return ApiResponse(success=True, data=annotation)


@annotation_router.post("/{episode_id}/review", summary="提交标注审核")
async def submit_review(
    episode_id: str,
    result: ReviewResult,
    service: ReviewServiceDep,
    session: SessionDep,
    user: Annotated[object, Depends(require_roles(Role.ANNOTATOR))],
) -> ApiResponse[Episode]:
    """通过 → ``published`` 并发 ``annotation.approved``；退回 → 回 ``annotation_pending`` 重做。"""
    if result.episode_id != episode_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="路径与请求体的 episode_id 不一致")
    outcome = await service.submit_review(result)
    await session.commit()
    return ApiResponse(success=True, data=outcome.episode)


__all__ = ["annotation_router", "verification_router"]
