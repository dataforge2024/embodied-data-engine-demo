"""Platform 应用入口。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from rdh_contract import __version__ as contract_version

from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import init_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动与关闭钩子。

    启动时先校验生产配置（默认凭据不得上生产），再建目录、表结构、种子用户。
    """
    settings = get_settings()
    settings.assert_production_ready()
    settings.ensure_dirs()
    await init_schema()

    # 幂等种子：无用户时创建 demo 用户（生产环境不会走到这里，assert_production_ready 拦住默认密码）
    from app.db.session import get_session_factory
    from app.services.seed import ensure_demo_users

    async with get_session_factory()() as session:
        users = await ensure_demo_users(session)
        await session.commit()
        if users:
            logger.info(
                "已创建 %d 个 demo 用户（%s）",
                len(users),
                "/".join(u.username for u in users),
            )

    logger.info(
        "Platform 启动 env=%s contract=%s queue=%s",
        settings.environment,
        contract_version,
        settings.event_queue_dir,
    )
    yield
    logger.info("Platform 关闭")


def create_app() -> FastAPI:
    """构造应用。"""
    settings = get_settings()
    app = FastAPI(
        title="RobotDataHub Platform API",
        version=contract_version,
        description="核心业务、WebSocket 服务、事件发布。契约见 contract/openapi/platform.yaml。",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()

__all__ = ["app", "create_app"]
