"""任务目录命名与元数据管理。

目录名格式：`<slug(任务名)>__<task_id>`，可读性优先。
`.task.json` 是权威数据源，目录名只是为了 `ls` 时可读。
"""

import json
import re
from pathlib import Path
from typing import NamedTuple

from rdh_contract.schemas import TaskRequirement


class TaskMetadata(NamedTuple):
    """任务元数据（存在 `.task.json` 中）。"""

    task_id: str
    task_name: str
    requirement: TaskRequirement
    uploaded_count: int = 0


def slugify(text: str, max_length: int = 60) -> str:
    """将任务名转换为文件系统安全的目录名片段。

    规则：
    - 路径分隔符与空白折叠为单个 `-`
    - 括号、尖括号、引号、管道符、问号、星号等特殊字符去除
    - 首尾的 `-` 和 `.` 去除（避免隐藏目录）
    - 连续 `-` 折叠为一个
    - 截断至 max_length 字符（按字符计，不按字节）
    - 结果为空时回退为 `task`

    保留中文：Agent 容器只跑 Linux，UTF-8 路径无问题。
    """
    # 路径分隔符与空白 → `-`
    slug = re.sub(r'[/\\:\s]+', '-', text)
    # 去除特殊字符（保留字母数字、中文、下划线、连字符、点号）
    slug = re.sub(r'[^\w.一-鿿-]+', '', slug)
    # 首尾 `-` `.` 去除
    slug = slug.strip('-.')
    # 连续 `-` 折叠
    slug = re.sub(r'-+', '-', slug)
    # 截断
    slug = slug[:max_length]
    # 回退
    return slug if slug else 'task'


def make_directory_name(task_name: str, task_id: str) -> str:
    """构造目录名：`<slug(任务名)>__<task_id>`。

    双下划线作分隔符（单个 `-` 或 `_` 在任务名中太常见）。

    Examples:
        >>> make_directory_name("厨房抓取/放置 (v2)", "t-a3f9c1")
        '厨房抓取-放置-v2__t-a3f9c1'
    """
    return f"{slugify(task_name)}__{task_id}"


def parse_directory_name(dir_name: str) -> str | None:
    """从目录名解析出 task_id（从右侧首个 `__` 切分）。

    Returns:
        task_id，解析失败返回 None。
    """
    parts = dir_name.rsplit('__', 1)
    return parts[1] if len(parts) == 2 else None


def read_task_metadata(task_dir: Path) -> TaskMetadata | None:
    """读取 `.task.json`。

    Returns:
        解析出的元数据，文件不存在或格式错误返回 None。
    """
    metadata_file = task_dir / '.task.json'
    if not metadata_file.exists():
        return None

    try:
        data = json.loads(metadata_file.read_text(encoding='utf-8'))
        return TaskMetadata(
            task_id=data['task_id'],
            task_name=data['task_name'],
            requirement=TaskRequirement(**data['requirement']),
            uploaded_count=data.get('uploaded_count', 0),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def write_task_metadata(task_dir: Path, metadata: TaskMetadata) -> None:
    """写入 `.task.json`（幂等，覆盖已存在的）。"""
    task_dir.mkdir(parents=True, exist_ok=True)
    metadata_file = task_dir / '.task.json'
    data = {
        'task_id': metadata.task_id,
        'task_name': metadata.task_name,
        'requirement': metadata.requirement.model_dump(mode='json'),
        'uploaded_count': metadata.uploaded_count,
    }
    metadata_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def get_task_id(task_dir: Path) -> str | None:
    """获取任务 ID，优先级：`.task.json` > 目录名解析。

    Returns:
        task_id，两者都失败返回 None。
    """
    meta = read_task_metadata(task_dir)
    if meta:
        return meta.task_id
    return parse_directory_name(task_dir.name)


def increment_uploaded_count(task_dir: Path) -> int:
    """`uploaded_count += 1` 并落盘，返回新值。

    元数据缺失或损坏时返回 0 且不写入 —— 计数只是展示用，
    权威进度在 Platform 侧（Agent 本地计数可能因目录被人为清理而失真）。
    """
    meta = read_task_metadata(task_dir)
    if meta is None:
        return 0
    updated = meta._replace(uploaded_count=meta.uploaded_count + 1)
    write_task_metadata(task_dir, updated)
    return updated.uploaded_count


__all__ = [
    'TaskMetadata',
    'slugify',
    'make_directory_name',
    'parse_directory_name',
    'read_task_metadata',
    'write_task_metadata',
    'get_task_id',
    'increment_uploaded_count',
]
