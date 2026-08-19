"""用户路由。前端需要 users 列表以显示 recorder 名字（recorded_by 是 user_id）。"""

from fastapi import APIRouter
from rdh_contract.schemas import ApiResponse, User

from app.api.dependencies import CurrentUserDep, UserRepoDep

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", summary="用户列表")
async def list_users(
    users: UserRepoDep,
    current_user: CurrentUserDep,
) -> ApiResponse[list[User]]:
    """返回全部用户。前端用于反查 recorded_by 对应的 display_name。"""
    return ApiResponse(success=True, data=list(await users.find_all()))


__all__ = ["router"]
