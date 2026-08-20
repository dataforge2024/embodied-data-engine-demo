"""Episode 处理流水线。

消费 ``episode.uploaded`` 后的编排（架构文档第三节的 4 类 worker 在此体现为 4 个阶段）：

1. **ingest** —— 解析 MCAP、建流索引（本地 demo 里由 ingest 阶段直接产出 streams）
2. **algo** —— 并发跑 4 个算子，每个是一个 K8s Job（本地为子进程）
3. **notify** —— 汇总结果回调 Platform（交互⑧）
4. **tool** —— 由 ``annotation.approved`` 事件另行触发，不在本流水线内

设计要点：**算子并发执行，任一失败不阻断其余**。质检算子挂了不该让预标注结果丢掉；
最终 ``pipeline_complete=True`` 一次性回调，Platform 据 ``all_succeeded`` 决定进核验还是失败。
"""

import asyncio
import logging
import uuid

from rdh_contract.enums import AlgoOperator, JobStatus
from rdh_contract.events import EpisodeUploaded
from rdh_contract.schemas.scheduler import AlgoJobResult

from scheduler.callbacks.platform import PlatformClient
from scheduler.config import Settings
from scheduler.k8s.job_builder import build_spec
from scheduler.k8s.runner import AlgoRunner

logger = logging.getLogger(__name__)

# 流水线包含的算子。顺序不重要（并发执行），但要稳定以便日志比对。
PIPELINE_OPERATORS: tuple[AlgoOperator, ...] = (
    AlgoOperator.QUALITY,
    AlgoOperator.KEYFRAME,
    AlgoOperator.PREANNOTATE,
    AlgoOperator.ANOMALY,
)


class EpisodePipeline:
    """Episode 处理流水线编排。"""

    def __init__(self, *, settings: Settings, runner: AlgoRunner, platform: PlatformClient) -> None:
        self._settings = settings
        self._runner = runner
        self._platform = platform

    async def handle_episode_uploaded(self, event: EpisodeUploaded) -> tuple[AlgoJobResult, ...]:
        """处理 ``episode.uploaded``：跑全部算子并回调 Platform。

        Platform 侧的 ``uploaded → processing`` 由它自己在收到本回调前完成 ——
        Scheduler 不直接改 Platform 的状态，只上报结果。
        """
        logger.info(
            "流水线启动 episode=%s object_key=%s size=%d",
            event.episode_id,
            event.object_key,
            event.size_bytes,
        )

        results = await self._run_operators(
            episode_id=event.episode_id, input_object_key=event.object_key
        )

        succeeded = sum(1 for r in results if r.status is JobStatus.SUCCEEDED)
        logger.info("算子完成 episode=%s 成功 %d/%d", event.episode_id, succeeded, len(results))

        await self._platform.report_algo_result(
            episode_id=event.episode_id, results=results, pipeline_complete=True
        )
        return results

    async def handle_annotation_processing(self, episode_id: str) -> bool:
        """送标处理：质检通过后准备标注数据，随后回调 Platform 推进状态。

        **本阶段不跑算子**（design.md 第 2 节）：4 个算子在解析阶段已经跑过一遍，
        「送标再跑什么」尚未定义。所以这里只是一个可见的异步环节 —— 状态本身有价值
        （送标中可见、失败有去处、可重试），内部跑什么可以后填。将来接算子时改的是
        本方法内部，状态机不用再动。

        失败也要回调：算子挂了而不上报，Episode 会静默留在 ``annotation_processing``，
        点过质检的人以为提交成功了 —— 这与 ``uploaded → processing`` 踩过的坑同源。

        返回是否成功。触发方式尚未收口：契约里没有对应事件，当前由调用方（demo /
        e2e）直接调用。
        """
        logger.info("送标处理启动 episode=%s", episode_id)
        try:
            await self._prepare_annotation_data(episode_id)
        except Exception as exc:
            logger.exception("送标处理失败 episode=%s", episode_id)
            await self._platform.report_annotation_processing(
                episode_id=episode_id, succeeded=False, error_message=f"送标处理失败：{exc}"
            )
            return False

        await self._platform.report_annotation_processing(episode_id=episode_id, succeeded=True)
        logger.info("送标处理完成 episode=%s", episode_id)
        return True

    async def _prepare_annotation_data(self, episode_id: str) -> None:
        """准备标注数据。

        本阶段是空操作 —— 解析阶段的 ``preannotate`` 产物已经落在 Episode 上，
        Tool 直接读它即可。留这个方法是为了让「送标要做什么」有一个明确的落点，
        接算子时不必再改 :meth:`handle_annotation_processing` 的错误处理与回调逻辑。
        """

    async def _run_operators(
        self, *, episode_id: str, input_object_key: str
    ) -> tuple[AlgoJobResult, ...]:
        """并发执行全部算子。

        用 ``return_exceptions=True``：一个算子抛异常不该让其余结果一起丢。
        执行器本身已把失败转成 ``AlgoJobResult``，这里兜住的是执行器自己的意外。
        """
        specs = [
            build_spec(
                job_id=str(uuid.uuid4()),
                episode_id=episode_id,
                operator=operator,
                input_object_key=input_object_key,
                registry=self._settings.algo_image_registry,
                model_version=self._settings.algo_model_version,
                timeout_seconds=self._settings.algo_job_timeout_seconds,
                ttl_seconds=self._settings.algo_job_ttl_seconds,
            )
            for operator in PIPELINE_OPERATORS
        ]

        outcomes = await asyncio.gather(
            *(self._runner.run(spec) for spec in specs), return_exceptions=True
        )

        results: list[AlgoJobResult] = []
        for spec, outcome in zip(specs, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                from datetime import UTC, datetime

                logger.exception("算子执行器异常 operator=%s", spec.operator.value)
                now = datetime.now(UTC)
                results.append(
                    AlgoJobResult(
                        job_id=spec.job_id,
                        episode_id=spec.episode_id,
                        operator=spec.operator,
                        status=JobStatus.FAILED,
                        model_version=self._settings.algo_model_version,
                        error_message=f"执行器异常：{outcome}",
                        started_at=now,
                        finished_at=now,
                    )
                )
            else:
                results.append(outcome)
        return tuple(results)


__all__ = ["PIPELINE_OPERATORS", "EpisodePipeline"]
