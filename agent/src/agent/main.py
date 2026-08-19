"""Agent 入口。

三种模式：

- ``--recover`` —— 只跑断电恢复，处理完退出
- ``--task-id <id>`` —— 采集一条 Episode 后退出（demo 与手工验证用）
- 默认 —— 常驻：WS 连接 + 心跳 + 等任务推送
"""

import argparse
import asyncio
import logging

from agent.collector import Collector
from agent.config import get_settings
from agent.platform_client import PlatformClient
from agent.recovery import RecoveryService
from agent.store.sqlite import StateStore
from agent.ws.client import AgentSocket

logger = logging.getLogger(__name__)


async def run(
    *, task_id: str | None, recover_only: bool, username: str, password: str, daemon: bool
) -> int:
    """执行 Agent。返回进程退出码。"""
    settings = get_settings()
    settings.ensure_dirs()
    store = StateStore(settings.state_db_path)

    platform = PlatformClient(
        base_url=settings.platform_base_url,
        agent_token=settings.agent_token,
        timeout_seconds=settings.request_timeout_seconds,
    )

    if not await platform.health():
        logger.error("Platform 不可用：%s", settings.platform_base_url)
        return 1

    # ---- 启动恢复：先把残局处理掉再接新活 ----
    report = await RecoveryService(settings=settings, store=store, platform=platform).recover()
    if report.had_work:
        logger.info(
            "恢复完成：续传 %d 条，补发回调 %d 条，失败 %d 条",
            report.resumed_uploads,
            report.resent_callbacks,
            len(report.failed),
        )
    if recover_only:
        return 0 if not report.failed else 1

    authed = platform.with_access_token(await platform.login(username, password))
    collector = Collector(settings=settings, store=store, platform=authed)

    if task_id is not None:
        outcome = await collector.collect_once(
            task_id=task_id, robot_model="rm-75-6f", scene="kitchen"
        )
        logger.info("采集完成 episode=%s", outcome.episode_id)
        if not daemon:
            return 0

    if daemon:
        await _run_daemon(settings=settings, collector=collector)
    return 0


async def _run_daemon(*, settings: object, collector: Collector) -> None:
    """常驻模式：WS 连接 + 心跳 + 目录监听。"""
    from rdh_contract.ws import TaskPushFrame, UploadTriggerFrame

    from agent.config import Settings
    from agent.file_processor import FileProcessor
    from agent.task_handler import TaskDirectoryManager
    from agent.watcher import DirectoryWatcher

    assert isinstance(settings, Settings)

    # 拉取已分派任务并重建目录
    tasks = await collector.platform.fetch_assigned_tasks(settings.agent_id)
    task_mgr = TaskDirectoryManager(settings.watch_root)
    task_mgr.rebuild_from_platform(tasks)
    if tasks:
        logger.info("重建 %d 个任务目录", len(tasks))

    # 文件处理器 + 目录监听
    processor = FileProcessor(
        uploader=collector._uploader,
        platform=collector._platform,
        store=collector._store,
        agent_id=settings.agent_id,
        chunk_size=settings.chunk_size_bytes,
    )
    watcher = DirectoryWatcher(
        watch_root=settings.watch_root,
        on_file_ready=processor.process,
        sample_interval_seconds=1.0,
        stable_sample_count=3,
    )
    watcher.start()
    existing_count = watcher.scan_existing()
    if existing_count:
        logger.info("启动扫描入队 %d 个已有文件", existing_count)

    socket = AgentSocket(
        url=settings.platform_ws_url,
        agent_id=settings.agent_id,
        hostname=settings.hostname,
        version=settings.version,
        reconnect_initial_seconds=settings.reconnect_initial_seconds,
        reconnect_max_seconds=settings.reconnect_max_seconds,
    )
    stop = asyncio.Event()

    async def on_frame(frame: object) -> None:
        logger.info("收到下行帧 %s", type(frame).__name__)
        if isinstance(frame, TaskPushFrame):
            task_mgr.handle_task_push(frame)
        elif isinstance(frame, UploadTriggerFrame):
            count = watcher.scan_existing()
            logger.info(
                "触发回传 task_id=%s reason=%s 入队=%d",
                frame.task_id or "全部",
                frame.reason or "手动触发",
                count,
            )

    def heartbeat_state() -> tuple[int, int, str | None]:
        return collector.pending_upload_count(), collector.disk_free_bytes(), None

    logger.info("Agent 常驻运行，Ctrl-C 退出")
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(watcher.run())
            tg.create_task(
                socket.run_with_reconnect(
                    on_frame=on_frame, heartbeat_provider=heartbeat_state, stop_event=stop
                )
            )
    except (asyncio.CancelledError, KeyboardInterrupt):
        watcher.stop()
        logger.info("监听已停止")


def main() -> int:
    """CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="RobotDataHub Agent")
    parser.add_argument("--task-id", help="采集一条 Episode 到指定任务")
    parser.add_argument("--recover", action="store_true", help="只跑断电恢复")
    parser.add_argument("--daemon", action="store_true", help="采集后转常驻")
    parser.add_argument("--username", default="admin", help="登录名")
    parser.add_argument("--password", default="demo-only-pass", help="密码")
    args = parser.parse_args()

    return asyncio.run(
        run(
            task_id=args.task_id,
            recover_only=args.recover,
            username=args.username,
            password=args.password,
            daemon=args.daemon,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run"]
