"""API 路由汇总。"""

from fastapi import APIRouter

from app.api.routes import agents, auth, callbacks, datasets, episodes, health, review, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(episodes.router)
api_router.include_router(tasks.router)
api_router.include_router(review.verification_router)
api_router.include_router(review.annotation_router)
api_router.include_router(datasets.router)
api_router.include_router(callbacks.router)
api_router.include_router(agents.router)

__all__ = ["api_router"]
