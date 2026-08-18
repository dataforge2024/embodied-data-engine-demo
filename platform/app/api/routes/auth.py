"""认证路由。"""

from fastapi import APIRouter
from rdh_contract.schemas import ApiResponse, LoginRequest, TokenResponse, User

from app.api.dependencies import AuthServiceDep, CurrentUserDep

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", summary="登录获取 JWT")
async def login(request: LoginRequest, service: AuthServiceDep) -> ApiResponse[TokenResponse]:
    """校验凭据并签发 JWT。失败统一返回 401，不区分用户名错还是密码错。"""
    return ApiResponse(success=True, data=await service.login(request))


@router.get("/me", summary="当前用户")
async def me(user: CurrentUserDep) -> ApiResponse[User]:
    """返回当前用户（不含任何凭据字段）。"""
    return ApiResponse(success=True, data=user)


__all__ = ["router"]
