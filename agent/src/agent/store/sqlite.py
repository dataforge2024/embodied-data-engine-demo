"""本地状态持久化（SQLite）。

**这是断电恢复的基础**：录制与上传的每一步都先落库再执行，进程被杀后能从库里
重建「哪些 Episode 还没传完、哪些分片已经传了」。

用同步 sqlite3 而非 aiosqlite：单机单进程、写入量小，异步带来的复杂度不值得。
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rdh_contract.enums import UploadStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id      TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    local_path      TEXT NOT NULL,
    object_key      TEXT,
    duration_ms     INTEGER,
    size_bytes      INTEGER,
    checksum        TEXT,
    recorded_topics TEXT NOT NULL DEFAULT '[]',
    upload_status   TEXT NOT NULL DEFAULT 'pending',
    total_parts     INTEGER NOT NULL DEFAULT 0,
    uploaded_parts  TEXT NOT NULL DEFAULT '[]',
    callback_done   INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_episodes_upload_status ON episodes (upload_status);
"""


@dataclass(frozen=True)
class EpisodeRecord:
    """本地 Episode 记录。"""

    episode_id: str
    task_id: str
    local_path: Path
    object_key: str | None
    duration_ms: int | None
    size_bytes: int | None
    checksum: str | None
    recorded_topics: tuple[str, ...]
    upload_status: UploadStatus
    total_parts: int
    uploaded_parts: tuple[int, ...]
    callback_done: bool
    last_error: str | None

    @property
    def missing_parts(self) -> tuple[int, ...]:
        """待续传分片。"""
        done = set(self.uploaded_parts)
        return tuple(p for p in range(1, self.total_parts + 1) if p not in done)

    @property
    def needs_upload(self) -> bool:
        """是否还需要上传。"""
        return self.upload_status is not UploadStatus.COMPLETED

    @property
    def needs_callback(self) -> bool:
        """上传完成但回调未成功 —— 恢复时要补发（交互③）。"""
        return self.upload_status is UploadStatus.COMPLETED and not self.callback_done


class StateStore:
    """本地状态库。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """打开连接。``isolation_level=None`` 用自动提交，避免忘记 commit 丢数据。"""
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record_episode(self, *, episode_id: str, task_id: str, local_path: Path) -> None:
        """登记新 Episode（录制开始时立刻写，先落库再录）。"""
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO episodes
                    (episode_id, task_id, local_path, upload_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (episode_id, task_id, str(local_path), UploadStatus.PENDING.value, now, now),
            )

    def finish_recording(
        self,
        episode_id: str,
        *,
        duration_ms: int,
        size_bytes: int,
        checksum: str,
        recorded_topics: tuple[str, ...],
    ) -> None:
        """录制结束，记录文件元信息。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE episodes
                   SET duration_ms = ?, size_bytes = ?, checksum = ?,
                       recorded_topics = ?, updated_at = ?
                 WHERE episode_id = ?
                """,
                (
                    duration_ms,
                    size_bytes,
                    checksum,
                    json.dumps(list(recorded_topics)),
                    datetime.now(UTC).isoformat(),
                    episode_id,
                ),
            )

    def start_upload(self, episode_id: str, *, object_key: str, total_parts: int) -> None:
        """开始上传，记录分片总数。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE episodes
                   SET object_key = ?, total_parts = ?, upload_status = ?, updated_at = ?
                 WHERE episode_id = ?
                """,
                (
                    object_key,
                    total_parts,
                    UploadStatus.IN_PROGRESS.value,
                    datetime.now(UTC).isoformat(),
                    episode_id,
                ),
            )

    def mark_part_uploaded(self, episode_id: str, part_number: int) -> tuple[int, ...]:
        """记录一个分片完成，返回已完成分片集合。

        **每传完一片就落库** —— 这是断点续传的关键，进程被杀最多重传一片。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT uploaded_parts FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Episode 未登记：{episode_id}")
            parts = sorted({*json.loads(row["uploaded_parts"]), part_number})
            conn.execute(
                "UPDATE episodes SET uploaded_parts = ?, updated_at = ? WHERE episode_id = ?",
                (json.dumps(parts), datetime.now(UTC).isoformat(), episode_id),
            )
        return tuple(parts)

    def complete_upload(self, episode_id: str) -> None:
        """标记上传完成。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE episodes SET upload_status = ?, last_error = NULL, updated_at = ? "
                "WHERE episode_id = ?",
                (UploadStatus.COMPLETED.value, datetime.now(UTC).isoformat(), episode_id),
            )

    def fail_upload(self, episode_id: str, *, error: str) -> None:
        """标记上传失败并记录原因。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE episodes SET upload_status = ?, last_error = ?, updated_at = ? "
                "WHERE episode_id = ?",
                (
                    UploadStatus.FAILED.value,
                    error[:500],
                    datetime.now(UTC).isoformat(),
                    episode_id,
                ),
            )

    def mark_callback_done(self, episode_id: str) -> None:
        """标记上传回调已成功（交互③完成）。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE episodes SET callback_done = 1, updated_at = ? WHERE episode_id = ?",
                (datetime.now(UTC).isoformat(), episode_id),
            )

    def get(self, episode_id: str) -> EpisodeRecord | None:
        """按 ID 查询。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM episodes WHERE episode_id = ?", (episode_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def unfinished(self) -> tuple[EpisodeRecord, ...]:
        """未完成的 Episode（断电恢复扫这个）。

        包含两类：上传没传完的，以及传完了但回调没成功的。
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM episodes
                 WHERE upload_status != ? OR callback_done = 0
                 ORDER BY created_at
                """,
                (UploadStatus.COMPLETED.value,),
            ).fetchall()
        records = tuple(_row_to_record(r) for r in rows)
        return tuple(r for r in records if r.needs_upload or r.needs_callback)

    def pending_upload_count(self) -> int:
        """待上传数量，心跳里上报。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM episodes WHERE upload_status != ?",
                (UploadStatus.COMPLETED.value,),
            ).fetchone()
        return int(row["c"])


def _row_to_record(row: sqlite3.Row) -> EpisodeRecord:
    """SQLite 行 → 记录。"""
    return EpisodeRecord(
        episode_id=row["episode_id"],
        task_id=row["task_id"],
        local_path=Path(row["local_path"]),
        object_key=row["object_key"],
        duration_ms=row["duration_ms"],
        size_bytes=row["size_bytes"],
        checksum=row["checksum"],
        recorded_topics=tuple(json.loads(row["recorded_topics"])),
        upload_status=UploadStatus(row["upload_status"]),
        total_parts=row["total_parts"],
        uploaded_parts=tuple(json.loads(row["uploaded_parts"])),
        callback_done=bool(row["callback_done"]),
        last_error=row["last_error"],
    )


__all__ = ["SCHEMA", "EpisodeRecord", "StateStore"]
