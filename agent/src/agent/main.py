"""Agent 入口。

三种模式：

- ``--recover`` —— 只跑断电恢复，处理完退出
- ``--task-id <id>`` —— 采集一条 Episode 后退出（demo 与手工验证用）
- 默认 —— 常驻：WS 连接 + 心跳 + 等任务推送
"""

import argparse
import asyncio
import contextlib
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
    """常驻模式：保持 WS 连接与心跳。"""
    from agent.config import Settings

    assert isinstance(settings, Settings)
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

    def heartbeat_state() -> tuple[int, int, str | None]:
        return collector.pending_upload_count(), collector.disk_free_bytes(), None

    logger.info("Agent 常驻运行，Ctrl-C 退出")
    with contextlib.suppress(asyncio.CancelledError, KeyboardInterrupt):
        await socket.run_with_reconnect(
            on_frame=on_frame, heartbeat_provider=heartbeat_state, stop_event=stop
        )


def main() -> int:
    """CLI 入口。"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description="RobotDataHub Agent")
    parser.add_argument("--task-id", help="采集一条 Episode 到指定任务")
    parser.add_argument("--recover", action="store_true", help="只跑断电恢复")
    parser.add_argument("--daemon", action="store_true", help="采集后转常驻")
    parser.add_argument("--username", default="recorder", help="登录名")
    parser.add_argument("--password", default="recorder-local-pass", help="密码")
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
