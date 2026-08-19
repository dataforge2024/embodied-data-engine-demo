"""Worker 主循环。

架构文档里的 4 类 worker（ingest / tool / algo / notify）在本地实现为**一个进程内的
4 个消费循环**，各自订阅契约里声明归属自己的 routing_key。生产环境是 4 组独立
Celery worker + KEDA 按队列深度伸缩（0~150 副本，见 ``deploy/keda-scaledobject.yaml``）。

一个进程还是四组进程，消费逻辑完全相同 —— 靠 ``routing_keys_for_queue()`` 分派。
"""

import asyncio
import logging
from pathlib import Path

from rdh_contract.enums import JobType
from rdh_contract.events import (
    AnnotationApproved,
    DatasetBuildRequested,
    EpisodeRejected,
    EpisodeUploaded,
    routing_keys_for_queue,
)

from scheduler.callbacks.platform import PlatformClient
from scheduler.config import Settings, get_settings
from scheduler.consumers.queue import ConsumedEvent, FileQueueConsumer
from scheduler.k8s.runner import AlgoRunner, KubernetesRunner, SubprocessRunner
from scheduler.pipelines.episode_pipeline import EpisodePipeline

logger = logging.getLogger(__name__)

ALGO_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "algo"


def build_runner(settings: Settings) -> AlgoRunner:
    """按配置选择算子执行器。"""
    if settings.algo_runner == "k8s":
        from scheduler.k8s.job_builder import NAMESPACE

        return KubernetesRunner(namespace=NAMESPACE)
    return SubprocessRunner(
        algo_root=ALGO_ROOT,
        object_store_root=settings.object_store_root,
        timeout_seconds=settings.algo_job_timeout_seconds,
    )


class Worker:
    """单个队列的消费循环。"""

    def __init__(self, *, queue: JobType, settings: Settings, pipeline: EpisodePipeline) -> None:
        self._queue = queue
        self._settings = settings
        self._pipeline = pipeline
        self._consumer = FileQueueConsumer(
            queue_dir=settings.event_queue_dir,
            dlq_dir=settings.dlq_dir,
            processed_dir=settings.processed_dir,
            queue_name=queue.value,
        )
        self._subscribed = routing_keys_for_queue(queue)

    @property
    def consumer(self) -> FileQueueConsumer:
        """暴露消费者，供测试与 drain 断言。"""
        return self._consumer

    async def handle(self, event: ConsumedEvent) -> None:
        """按 routing_key 分派处理。"""
        if isinstance(event.payload, EpisodeUploaded):
            await self._pipeline.handle_episode_uploaded(event.payload)

        elif isinstance(event.payload, AnnotationApproved):
            # tool-worker：格式转换与训练集并入。本阶段只记录，不实现导出格式
            logger.info(
                "标注已通过，待并入训练集 episode=%s segments=%d",
                event.payload.episode_id,
                event.payload.segment_count,
            )

        elif isinstance(event.payload, EpisodeRejected):
            # notify-worker：通知采集人重采
            logger.info(
                "Episode 被打回 episode=%s reason=%s",
                event.payload.episode_id,
                event.payload.reason,
            )

        elif isinstance(event.payload, DatasetBuildRequested):
            # tool-worker：训练集构建未实现（导出格式单开 change）。明确标示，
            # 而不是静默成功让上游以为已经建好
            logger.warning(
                "训练集构建尚未实现，请求已记录 dataset=%s format=%s episodes=%d",
                event.payload.dataset_id,
                event.payload.output_format,
                len(event.payload.episode_ids),
            )

        else:
            # 契约里注册了新事件但这里没加分支 —— 静默消费会让事件像被处理了一样消失
            logger.warning("事件无对应处理分支，已消费但未处理 routing_key=%s", event.routing_key)

    async def drain(self) -> int:
        """把当前队列里的消息全部处理完，返回处理条数。

        用于 demo 与测试：不需要常驻循环也能推进流水线。
        """
        handled = 0
        while (event := self._consumer.fetch()) is not None:
            try:
                await self.handle(event)
            except Exception as exc:
                logger.exception("处理事件失败 routing_key=%s", event.routing_key)
                self._consumer.reject(event, reason=str(exc))
                continue
            self._consumer.ack(event)
            handled += 1
        return handled

    async def run_forever(self) -> None:
        """常驻消费循环。"""
        logger.info(
            "worker 启动 queue=%s 订阅 %s",
            self._queue.value,
            ", ".join(self._subscribed) or "（无）",
        )
        while True:
            handled = await self.drain()
            if handled == 0:
                await asyncio.sleep(self._settings.poll_interval_seconds)


def build_pipeline(settings: Settings | None = None) -> EpisodePipeline:
    """构造流水线。Celery task 与进程内 worker 共用。"""
    settings = settings or get_settings()
    platform = PlatformClient(
        base_url=settings.platform_base_url,
        scheduler_token=settings.scheduler_token,
        timeout_seconds=settings.callback_timeout_seconds,
    )
    return EpisodePipeline(settings=settings, runner=build_runner(settings), platform=platform)


def build_workers(settings: Settings | None = None) -> tuple[Worker, ...]:
    """构造 4 类 worker。"""
    settings = settings or get_settings()
    settings.ensure_dirs()
    pipeline = build_pipeline(settings)
    return tuple(Worker(queue=queue, settings=settings, pipeline=pipeline) for queue in JobType)


async def main() -> None:
    """启动全部 worker。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    workers = build_workers()
    await asyncio.gather(*(w.run_forever() for w in workers))


if __name__ == "__main__":
    asyncio.run(main())


__all__ = ["ALGO_ROOT", "Worker", "build_pipeline", "build_runner", "build_workers", "main"]
