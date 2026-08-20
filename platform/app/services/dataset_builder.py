"""训练集构建。

**产出 manifest 清单，不做真实格式转换**（design.md 第 5 节）：lerobot / rlds 的
格式规范本身工作量不小，单开 change。清单的价值在于演示时点「导出」有东西可看，
以及下游能据它找到所有产物。

清单不是可训练的数据集 —— 拿它不能直接喂给训练框架，这一点写在 manifest 自身的
``format_note`` 里，免得下游误用。
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from rdh_contract.enums import AlgoOperator, EpisodeStatus
from rdh_contract.schemas import Dataset, Episode

from app.repositories.dataset import DatasetRepository
from app.repositories.episode import EpisodeRepository
from app.services.object_store import LocalObjectStore

logger = logging.getLogger(__name__)

MANIFEST_KEY_TEMPLATE = "datasets/{dataset_id}/manifest.json"

# 清单的结构版本。下游按它判断怎么解析，加字段时递增
MANIFEST_VERSION = 1


class DatasetBuildError(RuntimeError):
    """构建失败。调用方据此把 dataset 落 failed。"""


def _episode_entry(episode: Episode, *, object_store: LocalObjectStore) -> dict[str, Any]:
    """一条 Episode 在清单里的样子。

    分段取 Episode 上的那份 —— 它是人工标注后的最终版（``replace_segments`` 全量覆盖），
    而 Annotation 表里留的是审核轨迹，可能有多个修订版。
    """
    algo_artifacts: dict[str, str] = {}
    for operator in AlgoOperator:
        key = f"episodes/{episode.episode_id}/algo/{operator.value}/result.json"
        # 只列真的存在的：清单里挂一个取不到的键，下游会当成损坏
        if object_store.exists(key):
            algo_artifacts[operator.value] = key

    return {
        "episode_id": episode.episode_id,
        "task_id": episode.task_id,
        "status": episode.status.value,
        "object_key": episode.object_key,
        "checksum": episode.checksum,
        "size_bytes": episode.size_bytes,
        "duration_ms": episode.duration_ms,
        "robot_model": episode.robot_model,
        "scene": episode.scene,
        "recorded_by": episode.recorded_by,
        # 人工标注后的最终分段
        "segments": [
            {
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "action_label": segment.action_label,
                "description": segment.description,
                # source=None 表示人工改过，不再是算子产出
                "source": segment.source.value if segment.source else None,
            }
            for segment in episode.segments
        ],
        "key_frames": [
            {"timestamp_ms": frame.timestamp_ms, "object_key": frame.object_key}
            for frame in episode.key_frames
        ],
        "algo_artifacts": algo_artifacts,
    }


class DatasetBuilder:
    """构建训练集清单。

    读 Episode、写 manifest、推进 dataset 状态三件事都在这里 —— Scheduler 只是
    触发方，它没有 DB 访问（依赖铁律），拿不到人工标注后的分段。
    """

    def __init__(
        self,
        *,
        datasets: DatasetRepository,
        episodes: EpisodeRepository,
        object_store: LocalObjectStore,
    ) -> None:
        self._datasets = datasets
        self._episodes = episodes
        self._object_store = object_store

    async def build(self, dataset_id: str) -> Dataset:
        """构建清单并落库。

        失败时把 dataset 落 ``failed`` 再抛 —— 不落的话调用方查到的一直是
        ``running``，等一个永远不会来的结果。
        """
        dataset = await self._datasets.find_by_id(dataset_id)
        if dataset is None:
            raise DatasetBuildError(f"Dataset 不存在：{dataset_id}")

        await self._datasets.mark_running(dataset_id)

        try:
            manifest_key = await self._write_manifest(dataset)
        except Exception as exc:
            logger.exception("训练集构建失败 dataset=%s", dataset_id)
            await self._datasets.mark_failed(dataset_id, reason=f"构建失败：{exc}")
            raise DatasetBuildError(str(exc)) from exc

        built = await self._datasets.mark_succeeded(dataset_id, manifest_key=manifest_key)
        logger.info(
            "训练集构建完成 dataset=%s episodes=%d manifest=%s",
            dataset_id,
            len(dataset.episode_ids),
            manifest_key,
        )
        return built

    async def _write_manifest(self, dataset: Dataset) -> str:
        """收集 Episode 并写出 manifest.json，返回对象键。"""
        entries: list[dict[str, Any]] = []
        for episode_id in dataset.episode_ids:
            episode = await self._episodes.find_by_id(episode_id)
            if episode is None:
                raise DatasetBuildError(f"Episode 不存在：{episode_id}")
            # 入口已校验过，这里再拦一次：受理到构建之间 Episode 状态可能变
            if episode.status is not EpisodeStatus.PUBLISHED:
                raise DatasetBuildError(
                    f"Episode {episode_id} 状态为 {episode.status.value}，非 published"
                )
            entries.append(_episode_entry(episode, object_store=self._object_store))

        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "dataset_id": dataset.dataset_id,
            "output_format": dataset.output_format,
            "requested_by": dataset.requested_by,
            "built_at": datetime.now(UTC).isoformat(),
            "episode_count": len(entries),
            "segment_count": sum(len(e["segments"]) for e in entries),
            "episodes": entries,
            "format_note": (
                f"本清单列出纳入 {dataset.output_format} 训练集的 Episode 与产物位置，"
                "并非可直接训练的打包数据；真实格式转换单开 change。"
            ),
        }

        manifest_key = MANIFEST_KEY_TEMPLATE.format(dataset_id=dataset.dataset_id)
        path = self._object_store.path_for(manifest_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再改名：构建中途挂掉不会留下半个清单被下游读到
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp_path.rename(path)
        return manifest_key


__all__ = ["MANIFEST_VERSION", "DatasetBuilder", "DatasetBuildError"]
