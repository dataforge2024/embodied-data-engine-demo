"""用户种子数据。

Platform 启动时自动检查，无用户时创建 demo 用户。密码经环境变量注入，
生产环境不调用此模块（assert_production_ready 会拦住默认密码）。
"""

import uuid
from datetime import UTC, datetime

from rdh_contract.enums import Role
from rdh_contract.schemas import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import UserRow
from app.repositories.user import row_to_user


async def ensure_demo_users(
    session: AsyncSession, password: str = "demo-only-pass"
) -> list[User]:
    """幂等创建 demo 用户，仅当用户表为空时。

    返回创建的用户列表（若已存在则返回空列表）。
    """
    from app.repositories.user import UserRepository

    repo = UserRepository(session)
    if await repo.count() > 0:
        return []

    pw_hash = hash_password(password)
    users = [
        UserRow(
            user_id=str(uuid.uuid4()),
            username="admin",
            display_name="管理员",
            password_hash=pw_hash,
            roles=[Role.ADMIN.value],
            active=True,
            created_at=datetime.now(UTC),
        ),
        UserRow(
            user_id=str(uuid.uuid4()),
            username="recorder",
            display_name="采集员",
            password_hash=pw_hash,
            roles=[Role.RECORDER.value],
            active=True,
            created_at=datetime.now(UTC),
        ),
    ]

    for user in users:
        session.add(user)
    await session.flush()

    return [row_to_user(u) for u in users]


__all__ = ["ensure_demo_users"]
