"""Platform 配置。

所有可变项走环境变量，无硬编码密钥。本地 demo 用 SQLite + 文件队列 + 本地目录，
生产切 PostgreSQL + RabbitMQ + MinIO —— 换的是 URL，不是代码。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 仓库根目录（platform/ 的上一级），本地运行态数据都落在这下面
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

DEFAULT_RUNTIME_DIR = REPO_ROOT / ".runtime"


class Settings(BaseSettings):
    """运行配置。

    生产部署必须显式设置 ``RDH_JWT_SECRET`` / ``RDH_AGENT_TOKEN`` / ``RDH_SCHEDULER_TOKEN``；
    默认值仅供本地 demo，:meth:`assert_production_ready` 会拦住把默认值带上生产的情况。
    """

    model_config = SettingsConfigDict(
        env_prefix="RDH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="local", description="local / staging / production")
    api_prefix: str = Field(default="/api/v1", description="API 路径前缀")

    # ---- 存储 ----
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{DEFAULT_RUNTIME_DIR / 'platform.db'}",
        description="生产改为 postgresql+asyncpg://...",
    )
    object_store_root: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "objects",
        description="本地对象存储根目录；生产改用 MinIO endpoint",
    )

    # ---- 消息 ----
    event_queue_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "queue",
        description="本地文件队列目录；生产改用 RabbitMQ broker URL",
    )

    # ---- 凭据 ----
    jwt_secret: str = Field(default="local-dev-only-not-a-real-secret", description="JWT 签名密钥")
    jwt_ttl_seconds: int = Field(default=3600, gt=0, description="JWT 有效期")
    agent_token: str = Field(default="local-agent-token", description="Agent 回调凭据（交互③）")
    scheduler_token: str = Field(
        default="local-scheduler-token", description="Scheduler 回调凭据（交互⑧）"
    )

    # ---- WebSocket ----
    heartbeat_timeout_seconds: int = Field(default=45, gt=0, description="心跳超时判离线")

    @property
    def is_production(self) -> bool:
        """是否生产环境。"""
        return self.environment == "production"

    def assert_production_ready(self) -> None:
        """生产环境下拒绝使用默认凭据。

        启动时调用。宁可起不来，也不要带着 demo 密钥上线。
        """
        if not self.is_production:
            return
        defaults = {
            "RDH_JWT_SECRET": ("local-dev-only-not-a-real-secret", self.jwt_secret),
            "RDH_AGENT_TOKEN": ("local-agent-token", self.agent_token),
            "RDH_SCHEDULER_TOKEN": ("local-scheduler-token", self.scheduler_token),
        }
        offenders = [name for name, (default, actual) in defaults.items() if default == actual]
        if offenders:
            raise RuntimeError(f"生产环境必须显式设置以下环境变量：{', '.join(offenders)}")

    def ensure_dirs(self) -> None:
        """创建本地运行目录。"""
        self.object_store_root.mkdir(parents=True, exist_ok=True)
        self.event_queue_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url.startswith("sqlite"):
            DEFAULT_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """进程级单例配置。"""
    return Settings()


__all__ = ["DEFAULT_RUNTIME_DIR", "REPO_ROOT", "Settings", "get_settings"]
