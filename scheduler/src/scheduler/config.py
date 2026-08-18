"""Scheduler 配置。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_RUNTIME_DIR = REPO_ROOT / ".runtime"


class Settings(BaseSettings):
    """运行配置。

    队列目录与对象存储根目录必须与 Platform 一致 —— 本地替身靠共享文件系统通信。
    """

    model_config = SettingsConfigDict(
        env_prefix="RDH_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = Field(default="local")

    # ---- 消息（与 Platform 共享）----
    event_queue_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "queue", description="本地文件队列；生产改 RabbitMQ URL"
    )
    dlq_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "dlq", description="死信目录：重试耗尽的消息落这里"
    )
    processed_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "processed", description="已处理消息归档，便于排查"
    )

    # ---- 对象存储（与 Platform 共享）----
    object_store_root: Path = Field(default=DEFAULT_RUNTIME_DIR / "objects")

    # ---- Platform 回调（交互⑧）----
    platform_base_url: str = Field(default="http://127.0.0.1:8000/api/v1")
    scheduler_token: str = Field(
        default="local-scheduler-token", description="调用 Platform 回调的专用凭据"
    )
    callback_timeout_seconds: float = Field(default=10.0, gt=0)

    # ---- 算子执行（交互⑦的本地替身）----
    algo_runner: str = Field(
        default="subprocess",
        description="subprocess（本地）或 k8s（生产）。本地用子进程替代 K8s Job",
    )
    algo_job_timeout_seconds: int = Field(default=300, gt=0)
    algo_job_ttl_seconds: int = Field(default=300, ge=0, description="K8s Job 完成后自动清理延迟")
    algo_image_registry: str = Field(default="robotdatahub", description="算子镜像仓库前缀")
    algo_model_version: str = Field(default="v0.1.0", description="默认模型版本（镜像 tag）")

    # ---- 消费循环 ----
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    max_retries: int = Field(default=3, ge=0, description="兜底重试上限；单事件以契约声明为准")

    def ensure_dirs(self) -> None:
        """创建本地运行目录。"""
        for path in (self.event_queue_dir, self.dlq_dir, self.processed_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置。"""
    return Settings()


__all__ = ["DEFAULT_RUNTIME_DIR", "REPO_ROOT", "Settings", "get_settings"]
