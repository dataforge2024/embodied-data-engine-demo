"""文件在任务目录内的流转与采集要求预检。

子目录标记阶段，默认不删除原文件：

```
<任务目录>/
├── .task.json         任务元数据
├── ep_003.mcap        待处理
├── .uploading/        正在上传
├── .done/             上传 + 回调都成功
├── .failed/           上传失败（附 .error 说明）
├── .rejected/         预检不通过（附 .error 说明）
└── .cancelled/        任务取消时未开始上传的
```
"""

import shutil
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from rdh_contract.schemas import TaskRequirement

from agent.mcap_parser import McapMetadata


class Stage(StrEnum):
    """文件所处阶段，对应任务目录下的子目录名。"""

    UPLOADING = '.uploading'
    DONE = '.done'
    FAILED = '.failed'
    REJECTED = '.rejected'
    CANCELLED = '.cancelled'


# watchdog 与目录扫描都要忽略这些子目录
STAGE_DIRS = frozenset(s.value for s in Stage)

# 静默忽略的文件（不报错、不移动）
SILENT_IGNORE_SUFFIXES = frozenset({'.tmp', '.part', '.crdownload'})
SILENT_IGNORE_NAMES = frozenset({'.DS_Store', 'Thumbs.db', '.task.json'})


class PrecheckResult(NamedTuple):
    """预检结果。"""

    passed: bool
    missing_topics: tuple[str, ...] = ()

    @property
    def reason(self) -> str:
        """人可读的失败原因。"""
        if self.passed:
            return '通过'
        return f'缺少必需 topic：{", ".join(self.missing_topics)}'


def should_ignore(path: Path) -> bool:
    """是否静默忽略（不报错、不移动、不产生日志噪音）。"""
    if path.name in SILENT_IGNORE_NAMES:
        return True
    if path.suffix in SILENT_IGNORE_SUFFIXES:
        return True
    # 编辑器临时文件
    if path.name.startswith('~$') or path.name.startswith('.~'):
        return True
    # 点号开头的（含各阶段子目录里的文件）
    return path.name.startswith('.')


def precheck_topics(metadata: McapMetadata, requirement: TaskRequirement) -> PrecheckResult:
    """比对解析出的 topic 与任务要求。

    只校验 topic 存在性，不校验时长 —— 时长边界（刚好差 100ms）由核验环节人工裁量。
    额外的 topic 不影响通过。
    """
    recorded = set(metadata.topics)
    missing = tuple(t for t in requirement.required_topics if t not in recorded)
    return PrecheckResult(passed=not missing, missing_topics=missing)


def move_to_stage(
    source: Path, task_dir: Path, stage: Stage, *, error_message: str | None = None
) -> Path:
    """把文件移入阶段子目录，可选附带 `.error` 说明文件。

    Returns:
        移动后的路径。
    """
    target_dir = task_dir / stage.value
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name

    # 同名已存在时加数字后缀，不静默覆盖
    if target.exists():
        stem, suffix = source.stem, source.suffix
        counter = 1
        while target.exists():
            target = target_dir / f'{stem}.{counter}{suffix}'
            counter += 1

    shutil.move(str(source), str(target))

    if error_message:
        target.with_suffix(target.suffix + '.error').write_text(
            error_message, encoding='utf-8'
        )

    return target


def reject(source: Path, task_dir: Path, reason: str) -> Path:
    """拒绝文件：移入 `.rejected/` 并写说明。不创建 Episode。"""
    return move_to_stage(source, task_dir, Stage.REJECTED, error_message=reason)


def mark_failed(source: Path, task_dir: Path, *, stage_name: str, error: str) -> Path:
    """标记上传失败：移入 `.failed/` 并写说明。"""
    message = f'失败阶段：{stage_name}\n错误：{error}\n'
    return move_to_stage(source, task_dir, Stage.FAILED, error_message=message)


def list_pending_files(task_dir: Path) -> tuple[Path, ...]:
    """列出任务目录顶层待处理的 `*.mcap`（忽略阶段子目录与点号文件）。"""
    if not task_dir.is_dir():
        return ()
    return tuple(
        sorted(
            p
            for p in task_dir.iterdir()
            if p.is_file() and p.suffix == '.mcap' and not should_ignore(p)
        )
    )


def list_stage_files(task_dir: Path, stage: Stage) -> tuple[Path, ...]:
    """列出某阶段子目录下的 `*.mcap`。"""
    stage_dir = task_dir / stage.value
    if not stage_dir.is_dir():
        return ()
    return tuple(sorted(p for p in stage_dir.iterdir() if p.is_file() and p.suffix == '.mcap'))


__all__ = [
    'STAGE_DIRS',
    'PrecheckResult',
    'Stage',
    'list_pending_files',
    'list_stage_files',
    'mark_failed',
    'move_to_stage',
    'precheck_topics',
    'reject',
    'should_ignore',
]
