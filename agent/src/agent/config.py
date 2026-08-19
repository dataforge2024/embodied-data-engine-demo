"""Agent 配置。"""

import socket
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_RUNTIME_DIR = REPO_ROOT / ".runtime"


class Settings(BaseSettings):
    """运行配置。"""

    model_config = SettingsConfigDict(
        env_prefix="RDH_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    agent_id: str = Field(default="agent-local-01", description="Agent 唯一标识")
    hostname: str = Field(default_factory=socket.gethostname, description="主机名")
    version: str = Field(default="0.1.0", description="Agent 版本")

    # ---- Platform 连接 ----
    platform_base_url: str = Field(default="http://127.0.0.1:8000/api/v1")
    platform_ws_url: str = Field(default="ws://127.0.0.1:8000/api/v1/ws/agent")
    agent_token: str = Field(default="local-agent-token", description="上传回调凭据（交互③）")
    request_timeout_seconds: float = Field(default=10.0, gt=0)

    # ---- 本地存储 ----
    recording_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "agent" / "recordings", description="MCAP 落盘目录"
    )
    state_db_path: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "agent" / "state.sqlite",
        description="本地状态库，断电恢复靠它",
    )
    object_store_root: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "objects",
        description="本地对象存储（替代 MinIO），须与 Platform 一致",
    )
    watch_root: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "agent" / "tasks",
        description="任务目录的父目录 —— 采集软件在此建 <任务名>__<task_id>/，Agent 监听",
    )

    # ---- 上传 ----
    chunk_size_bytes: int = Field(default=256 * 1024, gt=0, description="分片大小")
    max_upload_retries: int = Field(default=3, ge=0, description="单分片重试上限")

    # ---- 目录监听 ----
    sample_interval_seconds: float = Field(
        default=1.0, gt=0, description="文件大小采样间隔"
    )
    stable_sample_count: int = Field(
        default=3, ge=1, description="连续多少次大小不变视为写入完成"
    )

    # ---- WebSocket ----
    reconnect_initial_seconds: float = Field(default=1.0, gt=0, description="重连退避初值")
    reconnect_max_seconds: float = Field(default=30.0, gt=0, description="重连退避上限")


    def ensure_dirs(self) -> None:
        """创建本地目录。"""
        self.recording_dir.mkdir(parents=True, exist_ok=True)
        self.state_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.object_store_root.mkdir(parents=True, exist_ok=True)
        self.watch_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置。"""
    return Settings()


__all__ = ["DEFAULT_RUNTIME_DIR", "REPO_ROOT", "Settings", "get_settings"]
