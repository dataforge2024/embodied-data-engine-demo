"""模拟采集软件往任务目录写 MCAP，驱动常驻 Agent 的监听链路。

真实场景里这一步是录制软件在干的事：Platform 分派任务 → Agent 建好
`<任务名>__<task_id>/` 目录 → 录制软件往里写 MCAP → Agent 发现并上传。
本脚本替代「录制软件」这一环，其余链路都是真的。

用法::

    python scripts/mock_record.py 厨房抓取          # 任务名模糊匹配
    python scripts/mock_record.py 厨房抓取 -n 3     # 连写 3 条
    python scripts/mock_record.py --list           # 只列出可选任务

写完即退出。Agent 那边要等文件大小连续若干次采样不变才动手（默认 3 秒），
所以文件落地后过几秒才会看到上传开始。加 ``--done-marker`` 可以跳过这段等待。
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

for extra in (REPO_ROOT / "contract" / "src", REPO_ROOT / "agent" / "src"):
    sys.path.append(str(extra))

from agent.recorder.mcap_writer import record_simulated_episode  # noqa: E402
from agent.task_directory import read_task_metadata  # noqa: E402
from agent.watcher import DONE_MARKER_SUFFIX  # noqa: E402

WATCH_ROOT = REPO_ROOT / ".runtime" / "agent" / "tasks"

# 录制器实际产出的 topic（见 recorder/mcap_writer.py 的 SIMULATED_TOPICS）。
# 任务要求里的 required_topics 必须是它的子集，否则 Agent 预检会拒收。
PRODUCED_TOPICS = frozenset(
    {
        "/camera/front/image_raw",
        "/camera/wrist/image_raw",
        "/joint_states",
        "/gripper/state",
        "/force_torque",
    }
)


class TaskDir:
    """一个任务目录及其元数据。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.meta = read_task_metadata(path)

    @property
    def name(self) -> str:
        return self.meta.task_name if self.meta else self.path.name

    @property
    def task_id(self) -> str | None:
        return self.meta.task_id if self.meta else None


def discover_tasks() -> list[TaskDir]:
    """列出监听根目录下的任务目录，按修改时间倒序（最近建的在前）。"""
    if not WATCH_ROOT.is_dir():
        return []
    dirs = [
        d for d in WATCH_ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")
    ]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return [TaskDir(d) for d in dirs]


def match_task(tasks: list[TaskDir], keyword: str) -> TaskDir:
    """按任务名或 task_id 匹配。

    精确匹配优先于子串匹配 —— 否则短名字是长名字的前缀时（「厨房抓取-放置」与
    「厨房抓取-放置(UI重构验证)」），短的那个永远选不中。

    只在子串匹配到多个时才报错。
    """
    lowered = keyword.lower()

    exact = [t for t in tasks if t.name.lower() == lowered or t.task_id == keyword]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise SystemExit(
            f"「{keyword}」有多个同名任务，请改用 task_id：\n"
            + "\n".join(f"  - {t.name}  {t.task_id}" for t in exact)
        )

    hits = [
        t
        for t in tasks
        if lowered in t.name.lower() or lowered in (t.task_id or "").lower()
    ]
    if not hits:
        raise SystemExit(
            f"没有匹配「{keyword}」的任务目录。\n"
            f"可选：{', '.join(t.name for t in tasks) or '（无，请先在任务管理页建任务并分派）'}"
        )
    if len(hits) > 1:
        raise SystemExit(
            f"「{keyword}」匹配到多个任务，请写全名或用 task_id：\n"
            + "\n".join(f"  - {t.name}  {t.task_id}" for t in hits)
        )
    return hits[0]


def print_tasks(tasks: list[TaskDir]) -> None:
    """列出可选任务，顺带标出 topic 对不上的（跑之前就能看出会被拒）。"""
    if not tasks:
        print("监听根目录下没有任务目录。")
        print(f"  {WATCH_ROOT}")
        print("请先在任务管理页新建任务并分派给在线 Agent。")
        return

    print(f"任务目录（{WATCH_ROOT}）：\n")
    for task in tasks:
        missing = missing_topics(task)
        flag = "  ⚠ 会被拒收" if missing else ""
        print(f"  {task.name}{flag}")
        print(f"    task_id  {task.task_id or '（.task.json 缺失）'}")
        if task.meta:
            print(f"    要求     {', '.join(task.meta.requirement.required_topics)}")
        if missing:
            print(f"    缺 topic {', '.join(missing)} —— 录制器不产这些")
        print()


def missing_topics(task: TaskDir) -> tuple[str, ...]:
    """任务要求里录制器产不出来的 topic。非空则 Agent 预检必拒。"""
    if task.meta is None:
        return ()
    return tuple(
        t for t in task.meta.requirement.required_topics if t not in PRODUCED_TOPICS
    )


def pick_duration(task: TaskDir, requested: int | None) -> int:
    """选录制时长。默认取任务要求区间的中点，避免踩上下限边界。"""
    if requested is not None:
        return requested
    if task.meta is None:
        return 8000
    req = task.meta.requirement
    return (req.min_duration_ms + req.max_duration_ms) // 2


def write_episode(task: TaskDir, *, duration_ms: int, done_marker: bool) -> Path:
    """往任务目录顶层写一个 MCAP。

    先写成点号开头的临时名再改名 —— Agent 忽略点号文件，这样 watchdog 只会在
    文件完整后看到它一次，不必依赖大小采样去猜写完没写完。
    """
    final = task.path / f"episode-{uuid.uuid4().hex[:8]}.mcap"
    staging = task.path / f".writing-{final.name}"

    stats = record_simulated_episode(
        staging, episode_id=str(uuid.uuid4()), duration_ms=duration_ms
    )
    staging.rename(final)

    print(f"  ✓ {final.name}")
    print(
        f"    {stats.message_count} 条消息 · {stats.duration_ms}ms · "
        f"{stats.size_bytes / 1024:.0f} KiB"
    )
    print(f"    topics: {', '.join(stats.topics)}")

    if done_marker:
        marker = final.with_suffix(final.suffix + DONE_MARKER_SUFFIX)
        marker.touch()
        print(f"    已放完成标记 {marker.name}（Agent 立即处理，不等采样）")

    return final


def main() -> int:
    parser = argparse.ArgumentParser(
        description="模拟采集软件往任务目录写 MCAP，驱动 Agent 的监听链路。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python scripts/mock_record.py --list\n"
            "  python scripts/mock_record.py 厨房抓取\n"
            "  python scripts/mock_record.py 厨房抓取 -n 3 --done-marker\n"
        ),
    )
    parser.add_argument(
        "task", nargs="?", help="任务名（模糊匹配，也可传 task_id）"
    )
    parser.add_argument("--list", action="store_true", help="列出可选任务后退出")
    parser.add_argument("-n", "--count", type=int, default=1, help="写几条（默认 1）")
    parser.add_argument(
        "--duration-ms", type=int, help="录制时长，默认取任务要求区间的中点"
    )
    parser.add_argument(
        "--done-marker",
        action="store_true",
        help="附带完成标记文件，Agent 跳过大小采样立即处理",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="topic 对不上也照写（用于验证预检拒收）",
    )
    args = parser.parse_args()

    tasks = discover_tasks()

    if args.list or args.task is None:
        print_tasks(tasks)
        return 0 if args.list else 2

    if args.count < 1:
        raise SystemExit("--count 至少为 1")

    task = match_task(tasks, args.task)
    print(f"任务：{task.name}")
    print(f"目录：{task.path}\n")

    missing = missing_topics(task)
    if missing and not args.force:
        raise SystemExit(
            f"任务要求的 topic 录制器产不出来：{', '.join(missing)}\n"
            f"录制器只产：{', '.join(sorted(PRODUCED_TOPICS))}\n"
            "写进去会被 Agent 预检拒收（移入 .rejected/）。\n"
            "改任务要求，或加 --force 强行写入以验证拒收路径。"
        )
    if missing:
        print(f"⚠ 缺 topic {', '.join(missing)}，预期会被拒收（--force）\n")

    duration = pick_duration(task, args.duration_ms)
    for index in range(args.count):
        if args.count > 1:
            print(f"[{index + 1}/{args.count}]")
        write_episode(task, duration_ms=duration, done_marker=args.done_marker)

    wait_hint = "立即" if args.done_marker else "约 3 秒后"
    print(f"\n写入完成。Agent 会在{wait_hint}开始处理：")
    print("  tail -f /tmp/rdh-agent.log")
    print("采集记录页能看到 Episode 出现、进度条走完、状态翻到 uploaded。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
