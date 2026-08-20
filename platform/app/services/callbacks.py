"""回调处理（交互③ 与 交互⑧）。

两个回调**语义完全不同**，共用一个服务类只是为了聚合「外部系统写回」这一类逻辑：

- :meth:`CallbackService.handle_upload_complete` —— Agent 调用，驱动 ``uploading → uploaded``
- :meth:`CallbackService.handle_algo_result` —— Scheduler 调用，驱动
  ``processing → verification_pending / failed``
- :meth:`CallbackService.handle_annotation_processing` —— Scheduler 调用，驱动
  ``annotation_processing → annotation_pending / failed``
"""

from rdh_contract.enums import AlgoOperator
from rdh_contract.schemas import KeyFrame, QualityReport, Segment
from rdh_contract.schemas.agent import UploadCallback
from rdh_contract.schemas.scheduler import AlgoResultCallback, AnnotationProcessingCallback

from app.repositories.algo_job_run import AlgoJobRunRepository
from app.repositories.episode import EpisodeRepository
from app.repositories.task import TaskRepository
from app.services.episode_lifecycle import EpisodeLifecycleService, TransitionOutcome
from app.services.object_store import ObjectStore


class ChecksumMismatchError(ValueError):
    """上传文件的 checksum 与 Agent 声明不符。上层转 422。"""


class CallbackService:
    """外部系统写回编排。"""

    def __init__(
        self,
        *,
        lifecycle: EpisodeLifecycleService,
        episodes: EpisodeRepository,
        tasks: TaskRepository,
        object_store: ObjectStore,
        algo_job_runs: AlgoJobRunRepository,
    ) -> None:
        self._lifecycle = lifecycle
        self._episodes = episodes
        self._tasks = tasks
        self._store = object_store
        self._algo_job_runs = algo_job_runs

    async def handle_upload_complete(
        self, callback: UploadCallback, *, verify_checksum: bool = True
    ) -> TransitionOutcome:
        """交互③：Agent 上传完成。

        服务端**独立重算 checksum**，不信任 Agent 的声明 —— 传输截断或磁盘错误
        都会导致文件损坏，而 Agent 自己算的值无法证明落盘内容正确。
        """
        if verify_checksum and self._store.exists(callback.object_key):
            actual = self._store.compute_checksum(callback.object_key)
            if actual != callback.checksum:
                raise ChecksumMismatchError(
                    f"checksum 不符：声明 {callback.checksum[:12]}…，实际 {actual[:12]}…"
                )

        outcome = await self._lifecycle.mark_uploaded(
            callback.episode_id,
            object_key=callback.object_key,
            size_bytes=callback.size_bytes,
            checksum=callback.checksum,
            duration_ms=callback.duration_ms,
            recorded_topics=callback.recorded_topics,
        )
        if outcome.changed:
            await self._tasks.increment_counters(outcome.episode.task_id, collected=1)
        return outcome

    async def handle_algo_result(self, callback: AlgoResultCallback) -> TransitionOutcome:
        """交互⑧：Scheduler 汇报算子结果。

        每个算子的运行记录先落日志表（成功/失败都记，供界面回溯这一阶段自动跑了
        什么），再把产物合入 Episode，最后按 ``pipeline_complete`` 决定是否推进
        状态 —— 单个算子完成只落数据，整条流水线完成才动状态。
        """
        segments: tuple[Segment, ...] | None = None
        key_frames: tuple[KeyFrame, ...] | None = None
        quality: QualityReport | None = None

        for result in callback.results:
            await self._algo_job_runs.record(callback.episode_id, result)
            if result.operator is AlgoOperator.PREANNOTATE and result.segments:
                segments = result.segments
            elif result.operator is AlgoOperator.KEYFRAME and result.key_frames:
                key_frames = result.key_frames
            elif result.operator is AlgoOperator.QUALITY and result.quality is not None:
                quality = result.quality

        if any(x is not None for x in (segments, key_frames, quality)):
            await self._episodes.attach_processing_result(
                callback.episode_id,
                segments=segments,
                key_frames=key_frames,
                quality=quality,
            )

        if not callback.pipeline_complete:
            # 仅落数据，状态不动
            episode = await self._episodes.find_by_id(callback.episode_id)
            assert episode is not None  # attach 已确认存在
            return TransitionOutcome(episode=episode, changed=False)

        errors = [r.error_message for r in callback.results if r.error_message]
        return await self._lifecycle.finish_processing(
            callback.episode_id,
            succeeded=callback.all_succeeded,
            error_message="；".join(errors) if errors else None,
        )

    async def handle_annotation_processing(
        self, callback: AnnotationProcessingCallback
    ) -> TransitionOutcome:
        """送标处理结束：``annotation_processing → annotation_pending / failed``。

        本阶段送标环节不跑算子（design.md 第 2 节），所以没有产物要落库 ——
        只推进状态。将来接算子时在这里补落库逻辑，与 :meth:`handle_algo_result` 一致。
        """
        return await self._lifecycle.finish_annotation_processing(
            callback.episode_id,
            succeeded=callback.succeeded,
            error_message=callback.error_message,
        )


__all__ = ["CallbackService", "ChecksumMismatchError"]
