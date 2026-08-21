"""Celery 应用与四个任务（架构文档第三节的 4 类 worker）。

**Celery 只管执行，不管收发领域事件。** Platform 用 aio-pika 发领域事件，
:class:`~scheduler.consumers.rabbit.RabbitConsumer` 消费后校验通过才 ``task.delay()``。
理由见 change 的 design.md 第 1 节：Celery protocol v2 的消息体与领域事件信封不兼容，
且契约里的队列名是 KEDA 的权威来源。

每个 task 的 ``max_retries`` 取自契约的事件声明 —— 不在这里硬编码。
"""

import asyncio
import logging
from typing import Any

from celery import Celery
from rdh_contract.enums import JobType
from rdh_contract.events import EVENT_REGISTRY, AnnotationProcessingRequested, EpisodeUploaded

from scheduler.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _max_retries(routing_key: str) -> int:
    """取契约声明的重试上限。"""
    return EVENT_REGISTRY[routing_key].max_retries


def build_celery_app(settings: Settings | None = None) -> Celery:
    """构造 Celery 应用。broker 与结果后端都用 RabbitMQ（POC 不需要独立 result 存储）。"""
    settings = settings or get_settings()
    app = Celery("robotdatahub-scheduler", broker=settings.amqp_url)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        # 结果没人取，存了只是垃圾
        task_ignore_result=True,
        # 任务完成才 ack：worker 被杀时任务回队列重投，不会静默丢失
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # 队列名取自契约的 JobType —— 与 KEDA 的 queueName 同源
        task_routes={
            "scheduler.ingest_episode": {"queue": f"celery.{JobType.INGEST.value}"},
            "scheduler.notify_rejected": {"queue": f"celery.{JobType.NOTIFY.value}"},
            "scheduler.convert_annotation": {"queue": f"celery.{JobType.TOOL.value}"},
            "scheduler.build_dataset": {"queue": f"celery.{JobType.TOOL.value}"},
            "scheduler.request_annotation_processing": {
                "queue": f"celery.{JobType.TOOL.value}"
            },
        },
    )
    return app


app = build_celery_app()


def _run(coro: Any) -> Any:
    """在 Celery 的同步 task 里跑协程。

    Celery 5 的 worker 是同步的，而流水线与回调都是 async。每个 task 起一个事件循环 ——
    POC 规模够用；真要压吞吐得换 gevent/eventlet 或 Celery 的 async 支持。
    """
    return asyncio.run(coro)


@app.task(
    name="scheduler.ingest_episode",
    bind=True,
    max_retries=_max_retries("episode.uploaded"),
    retry_backoff=True,
    retry_jitter=True,
)
def ingest_episode(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """ingest-worker：解析 MCAP、跑算子、回调 Platform。

    ``payload`` 是 ``EpisodeUploaded`` 的 JSON 形式 —— 消费层已按契约校验过，这里直接
    ``model_validate`` 不会失败。

    幂等靠消费本身：算子输出覆盖同名 object_key，回调撞状态机守卫返回 409 被当重放咽掉。
    重投的代价是浪费一次算力（见 design.md 第 2 节）。
    """
    from scheduler.worker import build_pipeline

    event = EpisodeUploaded.model_validate(payload)
    try:
        results = _run(build_pipeline().handle_episode_uploaded(event))
    except Exception as exc:
        logger.exception("流水线失败 episode=%s", event.episode_id)
        raise self.retry(exc=exc) from exc
    return {"episode_id": event.episode_id, "operator_count": len(results)}


@app.task(
    name="scheduler.notify_rejected",
    bind=True,
    max_retries=_max_retries("episode.rejected"),
    retry_backoff=True,
)
def notify_rejected(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """notify-worker：Episode 被打回，通知采集人重采。

    本阶段只记录 —— 真接通知渠道（IM / 邮件）不在本 change 范围。
    """
    logger.info(
        "Episode 被打回 episode=%s reason=%s",
        payload.get("episode_id"),
        payload.get("reason"),
    )
    return {"episode_id": payload.get("episode_id"), "notified": False}


@app.task(
    name="scheduler.convert_annotation",
    bind=True,
    max_retries=_max_retries("annotation.approved"),
    retry_backoff=True,
)
def convert_annotation(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """tool-worker：标注通过后格式转换、并入训练集。

    本阶段只记录，导出格式单开 change。
    """
    logger.info(
        "标注已通过，待并入训练集 episode=%s segments=%s",
        payload.get("episode_id"),
        payload.get("segment_count"),
    )
    return {"episode_id": payload.get("episode_id"), "converted": False}


@app.task(
    name="scheduler.request_annotation_processing",
    bind=True,
    max_retries=_max_retries("annotation.processing_requested"),
    retry_backoff=True,
)
def request_annotation_processing(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """tool-worker：核验通过后跑送标处理，结束时回调 Platform 推进状态。

    ``payload`` 是 ``AnnotationProcessingRequested`` 的 JSON 形式。失败与成功都由
    ``EpisodePipeline.handle_annotation_processing`` 内部回调 Platform 上报，
    这里只负责重试调度。
    """
    from scheduler.worker import build_pipeline

    event = AnnotationProcessingRequested.model_validate(payload)
    try:
        succeeded = _run(build_pipeline().handle_annotation_processing(event.episode_id))
    except Exception as exc:
        logger.exception("送标处理失败 episode=%s", event.episode_id)
        raise self.retry(exc=exc) from exc
    return {"episode_id": event.episode_id, "succeeded": succeeded}


@app.task(
    name="scheduler.build_dataset",
    bind=True,
    max_retries=_max_retries("dataset.build_requested"),
    retry_backoff=True,
)
def build_dataset(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """tool-worker：触发训练集构建。

    **构建本身在 Platform 做**：清单要写人工标注后的最终分段，那份数据只在 Platform
    的库里，Scheduler 按依赖铁律不能直连 DB。本 task 负责的是「什么时候建」——
    消费事件、失败重试；「建出什么」由 Platform 的 DatasetBuilder 决定。

    产物是 manifest 清单而非可训练的打包数据（design.md 第 5 节），
    lerobot / rlds 的真实格式转换单开 change。

    422 不重试：那是「清单里有 Episode 不存在或未发布」这类问题，重试多少次都一样，
    且 Platform 已把 dataset 落 failed。
    """
    from scheduler.callbacks.platform import PlatformCallbackError
    from scheduler.worker import build_platform_client

    dataset_id = str(payload.get("dataset_id") or "")
    if not dataset_id:
        logger.error("构建请求缺少 dataset_id，丢弃 payload=%s", payload)
        return {"dataset_id": None, "built": False, "reason": "missing_dataset_id"}

    try:
        result = _run(build_platform_client().trigger_dataset_build(dataset_id=dataset_id))
    except PlatformCallbackError as exc:
        # 422 是确定性失败，重试没意义；其余（超时、5xx）值得重试
        if "422" in str(exc):
            logger.error("训练集构建被拒，不重试 dataset=%s：%s", dataset_id, exc)
            return {"dataset_id": dataset_id, "built": False, "reason": str(exc)}
        logger.exception("训练集构建触发失败 dataset=%s", dataset_id)
        raise self.retry(exc=exc) from exc

    logger.info(
        "训练集构建完成 dataset=%s format=%s episodes=%d",
        dataset_id,
        payload.get("output_format"),
        len(payload.get("episode_ids") or ()),
    )
    return {"dataset_id": dataset_id, "built": True, "result": result}


# routing_key → Celery task。消费层按此分派，不硬编码 task 名。
TASK_BY_ROUTING_KEY = {
    "episode.uploaded": ingest_episode,
    "episode.rejected": notify_rejected,
    "annotation.approved": convert_annotation,
    "annotation.processing_requested": request_annotation_processing,
    "dataset.build_requested": build_dataset,
}


__all__ = [
    "TASK_BY_ROUTING_KEY",
    "app",
    "build_celery_app",
    "build_dataset",
    "convert_annotation",
    "ingest_episode",
    "notify_rejected",
    "request_annotation_processing",
]
