"""用户仓储。

``password_hash`` 只在本模块内部流转，不进入返回给上层的 contract 模型。
"""

from datetime import UTC, datetime

from rdh_contract.enums import Role
from rdh_contract.schemas import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRow


def row_to_user(row: UserRow) -> User:
    """ORM 行 → contract 模型（不含凭据字段）。"""
    return User(
        user_id=row.user_id,
        username=row.username,
        display_name=row.display_name,
        roles=tuple(Role(r) for r in row.roles),
        active=row.active,
        created_at=row.created_at,
    )


class UserRepository:
    """用户数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        password_hash: str,
        roles: tuple[Role, ...],
    ) -> User:
        """新建用户。密码哈希由调用方（services）生成。"""
        row = UserRow(
            user_id=user_id,
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            roles=[r.value for r in roles],
            active=True,
            created_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return row_to_user(row)

    async def find_by_id(self, user_id: str) -> User | None:
        """按 ID 查询。"""
        row = await self._session.get(UserRow, user_id)
        return row_to_user(row) if row else None

    async def find_by_username(self, username: str) -> User | None:
        """按登录名查询。"""
        row = await self._find_row_by_username(username)
        return row_to_user(row) if row else None

    async def get_credentials(self, username: str) -> tuple[User, str] | None:
        """取用户与密码哈希，供登录校验。

        这是唯一返回哈希的方法，调用方仅限 ``services/auth.py``。
        """
        row = await self._find_row_by_username(username)
        if row is None:
            return None
        return row_to_user(row), row.password_hash

    async def find_all(self) -> tuple[User, ...]:
        """全部用户，按登录名排序。demo 规模下不分页。"""
        stmt = select(UserRow).order_by(UserRow.username)
        rows = (await self._session.execute(stmt)).scalars().all()
        return tuple(row_to_user(r) for r in rows)

    async def count(self) -> int:
        """用户总数，用于判断是否需要初始化种子数据。"""
        from sqlalchemy import func

        return (await self._session.execute(select(func.count()).select_from(UserRow))).scalar_one()

    async def _find_row_by_username(self, username: str) -> UserRow | None:
        stmt = select(UserRow).where(UserRow.username == username)
        return (await self._session.execute(stmt)).scalar_one_or_none()


__all__ = ["UserRepository", "row_to_user"]
