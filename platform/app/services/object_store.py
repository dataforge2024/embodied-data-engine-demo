"""对象存储抽象（交互②的服务端侧）。

本地 demo 用文件系统替代 MinIO。保留 MinIO 的关键语义：对象键是扁平路径、
上传凭据有过期时间、服务端能独立校验 checksum。

生产替换点只有 :class:`LocalObjectStore`：换成 minio SDK 实现同样的 :class:`ObjectStore`
协议即可。Agent 侧的分片逻辑不受影响。
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

GRANT_TTL_SECONDS = 3600

# 对象键布局：episodes/<episode_id>/raw.mcap —— 按 episode 分目录，便于整条清理
OBJECT_KEY_TEMPLATE = "episodes/{episode_id}/raw.mcap"


class ObjectNotFoundError(KeyError):
    """对象不存在。"""


class ObjectStore(Protocol):
    """对象存储协议。"""

    def build_object_key(self, episode_id: str) -> str:
        """生成对象键。"""
        ...

    def exists(self, object_key: str) -> bool:
        """对象是否存在。"""
        ...

    def compute_checksum(self, object_key: str) -> str:
        """计算对象的 SHA-256。"""
        ...

    def size_of(self, object_key: str) -> int:
        """对象字节数。"""
        ...


class LocalObjectStore:
    """基于本地目录的对象存储（本地替身）。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        """存储根目录。"""
        return self._root

    def build_object_key(self, episode_id: str) -> str:
        """生成对象键。"""
        return OBJECT_KEY_TEMPLATE.format(episode_id=episode_id)

    def path_for(self, object_key: str) -> Path:
        """对象键 → 本地路径。

        拒绝越界键（``..`` 穿越），否则恶意键可写到存储根目录之外。
        """
        candidate = (self._root / object_key).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"非法对象键：{object_key}")
        return candidate

    def exists(self, object_key: str) -> bool:
        """对象是否存在。"""
        return self.path_for(object_key).is_file()

    def compute_checksum(self, object_key: str) -> str:
        """计算 SHA-256，分块读避免大文件占内存。"""
        path = self.path_for(object_key)
        if not path.is_file():
            raise ObjectNotFoundError(object_key)
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def size_of(self, object_key: str) -> int:
        """对象字节数。"""
        path = self.path_for(object_key)
        if not path.is_file():
            raise ObjectNotFoundError(object_key)
        return path.stat().st_size

    def issue_grant(self, object_key: str) -> tuple[str, datetime]:
        """签发上传凭据，返回 ``(目标地址, 过期时间)``。

        本地实现返回 ``file://`` 路径。真实环境返回 MinIO 预签名 URL ——
        关键点是 Agent 不持有长期对象存储凭据。
        """
        path = self.path_for(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        expires_at = datetime.now(UTC) + timedelta(seconds=GRANT_TTL_SECONDS)
        return f"file://{path}", expires_at


__all__ = [
    "GRANT_TTL_SECONDS",
    "OBJECT_KEY_TEMPLATE",
    "LocalObjectStore",
    "ObjectNotFoundError",
    "ObjectStore",
]
