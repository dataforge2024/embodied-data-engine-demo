"""任务推送与目录管理。"""

import logging
from pathlib import Path

from rdh_contract.ws import TaskPushFrame

from agent.task_directory import TaskMetadata, make_directory_name, write_task_metadata

logger = logging.getLogger(__name__)


class TaskDirectoryManager:
    """任务目录创建与恢复。"""

    def __init__(self, watch_root: Path) -> None:
        self.watch_root = watch_root

    def handle_task_push(self, frame: TaskPushFrame) -> Path:
        """处理 TaskPushFrame：创建目录 + 写元数据（幂等）。

        Returns:
            任务目录路径。
        """
        p = frame.payload
        dir_name = make_directory_name(p.task_name, p.task_id)
        task_dir = self.watch_root / dir_name

        meta = TaskMetadata(
            task_id=p.task_id,
            task_name=p.task_name,
            requirement=p.requirement,
            uploaded_count=0,
        )
        write_task_metadata(task_dir, meta)
        logger.info("任务目录已就绪 task_id=%s dir=%s", p.task_id, task_dir.name)
        return task_dir

    def rebuild_from_platform(self, tasks: list[dict]) -> None:
        """Agent 启动/重连时重建已分派任务的目录。

        参数 tasks 是 `GET /agents/me/tasks` 返回的任务列表，
        每项含 `task_id` / `name` / `requirement` / `status`。

        只处理 `status == "assigned"` 的任务。
        """
        for t in tasks:
            if t.get('status') != 'assigned':
                continue

            dir_name = make_directory_name(t['name'], t['task_id'])
            task_dir = self.watch_root / dir_name

            # 已存在且 .task.json 有效 → 跳过（不覆盖已采集的文件）
            if (task_dir / '.task.json').exists():
                logger.debug("任务目录已存在 task_id=%s", t['task_id'])
                continue

            # 不存在或损坏 → 重建
            from rdh_contract.schemas import TaskRequirement

            meta = TaskMetadata(
                task_id=t['task_id'],
                task_name=t['name'],
                requirement=TaskRequirement(**t['requirement']),
                uploaded_count=0,
            )
            write_task_metadata(task_dir, meta)
            logger.info("重建任务目录 task_id=%s dir=%s", t['task_id'], task_dir.name)


__all__ = ['TaskDirectoryManager']
