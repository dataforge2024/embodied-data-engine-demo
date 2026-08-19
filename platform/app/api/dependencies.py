"""FastAPI 依赖注入。

三类凭据严格分开（最小权限）：

- ``bearerAuth`` 用户 JWT → :func:`current_user` / :func:`require_roles`
- ``agentAuth`` Agent 专用 → :func:`require_agent_token`，只能访问上传回调
- ``schedulerAuth`` Scheduler 专用 → :func:`require_scheduler_token`，只能访问算子回调
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from rdh_contract.enums import Role
from rdh_contract.schemas import User
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import AuthError, decode_access_token, verify_service_token
from app.db.session import get_session
from app.repositories.agent_node import AgentNodeRepository
from app.repositories.annotation import AnnotationRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.repositories.user import UserRepository
from app.services.auth import AuthService
from app.services.callbacks import CallbackService
from app.services.episode_lifecycle import EpisodeLifecycleService
from app.services.event_publisher import EventPublisher, FileQueuePublisher
from app.services.object_store import LocalObjectStore, ObjectStore
from app.services.rabbit_publisher import RabbitPublisher
from app.services.review import ReviewService
from app.services.task import TaskService

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# ---- 基础设施 ----


# AMQP 连接必须跨请求复用，不能每请求新建 —— 按 URL 缓存发布器实例。
_RABBIT_PUBLISHERS: dict[str, RabbitPublisher] = {}


def get_publisher(settings: SettingsDep) -> EventPublisher:
    """事件发布器。``RDH_QUEUE_BACKEND=file|rabbit`` 决定用哪个后端。"""
    if not settings.uses_rabbit:
        return FileQueuePublisher(settings.event_queue_dir)
    publisher = _RABBIT_PUBLISHERS.get(settings.amqp_url)
    if publisher is None:
        publisher = RabbitPublisher(settings.amqp_url)
        _RABBIT_PUBLISHERS[settings.amqp_url] = publisher
    return publisher


async def close_publisher() -> None:
    """关闭已建立的 AMQP 连接。应用停机时由 lifespan 调用。"""
    for publisher in list(_RABBIT_PUBLISHERS.values()):
        await publisher.close()
    _RABBIT_PUBLISHERS.clear()


def get_object_store(settings: SettingsDep) -> ObjectStore:
    """对象存储。生产切 MinIO 实现时只改这里。"""
    return LocalObjectStore(settings.object_store_root)


PublisherDep = Annotated[EventPublisher, Depends(get_publisher)]
ObjectStoreDep = Annotated[ObjectStore, Depends(get_object_store)]


# ---- 仓储 ----


def get_episode_repo(session: SessionDep) -> EpisodeRepository:
    """Episode 仓储。"""
    return EpisodeRepository(session)


def get_task_repo(session: SessionDep) -> TaskRepository:
    """任务仓储。"""
    return TaskRepository(session)


def get_annotation_repo(session: SessionDep) -> AnnotationRepository:
    """标注仓储。"""
    return AnnotationRepository(session)


def get_user_repo(session: SessionDep) -> UserRepository:
    """用户仓储。"""
    return UserRepository(session)


def get_agent_repo(session: SessionDep, settings: SettingsDep) -> AgentNodeRepository:
    """Agent 节点仓储。"""
    return AgentNodeRepository(
        session, heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds
    )


EpisodeRepoDep = Annotated[EpisodeRepository, Depends(get_episode_repo)]
TaskRepoDep = Annotated[TaskRepository, Depends(get_task_repo)]
AnnotationRepoDep = Annotated[AnnotationRepository, Depends(get_annotation_repo)]
UserRepoDep = Annotated[UserRepository, Depends(get_user_repo)]
AgentRepoDep = Annotated[AgentNodeRepository, Depends(get_agent_repo)]


# ---- 服务 ----


def get_lifecycle(episodes: EpisodeRepoDep, publisher: PublisherDep) -> EpisodeLifecycleService:
    """Episode 生命周期服务 —— 状态变更的唯一入口。"""
    return EpisodeLifecycleService(episodes=episodes, publisher=publisher)


LifecycleDep = Annotated[EpisodeLifecycleService, Depends(get_lifecycle)]


def get_review_service(
    lifecycle: LifecycleDep,
    annotations: AnnotationRepoDep,
    episodes: EpisodeRepoDep,
    tasks: TaskRepoDep,
) -> ReviewService:
    """人工环节服务。"""
    return ReviewService(
        lifecycle=lifecycle, annotations=annotations, episodes=episodes, tasks=tasks
    )


def get_callback_service(
    lifecycle: LifecycleDep,
    episodes: EpisodeRepoDep,
    tasks: TaskRepoDep,
    store: ObjectStoreDep,
) -> CallbackService:
    """回调服务。"""
    return CallbackService(lifecycle=lifecycle, episodes=episodes, tasks=tasks, object_store=store)


def get_auth_service(users: UserRepoDep, settings: SettingsDep) -> AuthService:
    """认证服务。"""
    return AuthService(
        users=users, jwt_secret=settings.jwt_secret, jwt_ttl_seconds=settings.jwt_ttl_seconds
    )


def get_task_service(tasks: TaskRepoDep, agents: AgentRepoDep) -> TaskService:
    """任务服务。"""
    return TaskService(tasks=tasks, agents=agents)


ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
CallbackServiceDep = Annotated[CallbackService, Depends(get_callback_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
TaskServiceDep = Annotated[TaskService, Depends(get_task_service)]


# ---- 认证 ----


async def current_user(
    users: UserRepoDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """解析用户 JWT 并加载用户。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("缺少 Bearer token")
    payload = decode_access_token(authorization.removeprefix("Bearer "), secret=settings.jwt_secret)
    user = await users.find_by_id(payload.sub)
    if user is None or not user.active:
        raise AuthError("用户不存在或已停用")
    return user


CurrentUserDep = Annotated[User, Depends(current_user)]


def require_roles(
    *roles: Role,
) -> Callable[[User], Coroutine[Any, Any, User]]:
    """生成角色校验依赖。任一角色匹配即通过；``admin`` 视为通配。"""
    allowed = frozenset(roles)

    async def _check(user: CurrentUserDep) -> User:
        if Role.ADMIN in user.roles or allowed & frozenset(user.roles):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"需要以下角色之一：{', '.join(sorted(r.value for r in allowed))}",
        )

    return _check


async def require_agent_token(
    settings: SettingsDep,
    x_agent_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验 Agent 专用凭据（交互③）。"""
    if not verify_service_token(x_agent_token, settings.agent_token):
        raise AuthError("Agent 凭据无效")


async def require_scheduler_token(
    settings: SettingsDep,
    x_scheduler_token: Annotated[str | None, Header()] = None,
) -> None:
    """校验 Scheduler 专用凭据（交互⑧）。"""
    if not verify_service_token(x_scheduler_token, settings.scheduler_token):
        raise AuthError("Scheduler 凭据无效")


__all__ = [
    "AgentRepoDep",
    "AnnotationRepoDep",
    "AuthServiceDep",
    "CallbackServiceDep",
    "CurrentUserDep",
    "close_publisher",
    "EpisodeRepoDep",
    "LifecycleDep",
    "ObjectStoreDep",
    "PublisherDep",
    "ReviewServiceDep",
    "SessionDep",
    "SettingsDep",
    "TaskRepoDep",
    "TaskServiceDep",
    "UserRepoDep",
    "current_user",
    "require_agent_token",
    "require_roles",
    "require_scheduler_token",
]
