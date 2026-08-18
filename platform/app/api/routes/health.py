"""健康检查。"""

from typing import Any

from fastapi import APIRouter
from rdh_contract import __version__ as contract_version
from sqlalchemy import text

from app.api.dependencies import SessionDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
async def health(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """探活。

    真连一次数据库 —— 只回 200 而不验依赖，等于把故障藏起来。
    契约版本一并暴露，便于确认各模块用的是同一版契约。
    """
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "environment": settings.environment,
        "contract_version": contract_version,
    }


__all__ = ["router"]
