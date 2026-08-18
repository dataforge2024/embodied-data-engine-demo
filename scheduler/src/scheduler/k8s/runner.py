"""算子执行器（交互⑦的执行侧）。

两种实现共用 :class:`AlgoRunner` 协议：

- :class:`SubprocessRunner` —— 本地：直接跑 ``algo/`` 里的算子入口脚本
- :class:`KubernetesRunner` —— 生产：提交 Job 并轮询状态（本阶段未接集群，方法体明确抛错）

算子的**输入输出约定与执行方式无关**：从 ``input_object_key`` 读、往 ``output_prefix`` 写、
结果以 JSON 落盘。这个约定让两种执行器可以互换。
"""

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from rdh_contract.enums import JobStatus
from rdh_contract.schemas.scheduler import AlgoJobResult, AlgoJobSpec

logger = logging.getLogger(__name__)

RESULT_FILENAME = "result.json"


class AlgoRunner(Protocol):
    """算子执行器协议。"""

    async def run(self, spec: AlgoJobSpec) -> AlgoJobResult:
        """执行一个算子作业并返回结果。"""
        ...


def _failed_result(
    spec: AlgoJobSpec, *, started_at: datetime, message: str, status: JobStatus = JobStatus.FAILED
) -> AlgoJobResult:
    """构造失败结果。"""
    return AlgoJobResult(
        job_id=spec.job_id,
        episode_id=spec.episode_id,
        operator=spec.operator,
        status=status,
        model_version=spec.image.rsplit(":", 1)[-1],
        error_message=message,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


class SubprocessRunner:
    """本地子进程执行器。

    用 ``sys.executable -m`` 调用 ``algo/`` 的算子入口，工作目录设为 ``algo/``，
    因此 Scheduler 不 import Algo 的任何代码 —— 依赖铁律不破。
    """

    def __init__(self, *, algo_root: Path, object_store_root: Path, timeout_seconds: int) -> None:
        self._algo_root = algo_root
        self._store_root = object_store_root
        self._timeout = timeout_seconds

    async def run(self, spec: AlgoJobSpec) -> AlgoJobResult:
        """执行算子。超时、非零退出、结果不合契约都转成失败结果而非抛异常。"""
        started_at = datetime.now(UTC)
        output_dir = self._store_root / spec.output_prefix
        output_dir.mkdir(parents=True, exist_ok=True)

        env_overrides = {
            "RDH_JOB_ID": spec.job_id,
            "RDH_EPISODE_ID": spec.episode_id,
            "RDH_OPERATOR": spec.operator.value,
            "RDH_INPUT_PATH": str(self._store_root / spec.input_object_key),
            "RDH_OUTPUT_DIR": str(output_dir),
            "RDH_MODEL_VERSION": spec.image.rsplit(":", 1)[-1],
        }

        import os

        # 算子的公共库在 algo/src/ 下。镜像里 Dockerfile 把它拷到 /app 并设了 PYTHONPATH；
        # 本地跑子进程没有这一步，需要显式加上，否则算子 import algo_common 失败。
        search_paths = [str(self._algo_root), str(self._algo_root / "src")]
        existing = os.environ.get("PYTHONPATH", "")
        if existing:
            search_paths.append(existing)
        env_overrides["PYTHONPATH"] = os.pathsep.join(search_paths)

        env = {**os.environ, **env_overrides}

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                f"operators.{spec.operator.value}.main",
                cwd=str(self._algo_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return _failed_result(spec, started_at=started_at, message=f"算子启动失败：{exc}")

        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return _failed_result(
                spec,
                started_at=started_at,
                message=f"算子超时（>{self._timeout}s）",
                status=JobStatus.TIMEOUT,
            )

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
            return _failed_result(
                spec, started_at=started_at, message=f"算子退出码 {process.returncode}：{detail}"
            )

        result_path = output_dir / RESULT_FILENAME
        if not result_path.is_file():
            return _failed_result(
                spec, started_at=started_at, message=f"算子未产出 {RESULT_FILENAME}"
            )

        try:
            raw: dict[str, Any] = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _failed_result(spec, started_at=started_at, message=f"结果非法 JSON：{exc}")

        # 补齐编排侧才知道的字段：算子只负责产出业务数据
        raw.setdefault("job_id", spec.job_id)
        raw.setdefault("episode_id", spec.episode_id)
        raw.setdefault("operator", spec.operator.value)
        raw.setdefault("status", JobStatus.SUCCEEDED.value)
        raw.setdefault("model_version", spec.image.rsplit(":", 1)[-1])
        raw.setdefault("started_at", started_at.isoformat())
        raw.setdefault("finished_at", datetime.now(UTC).isoformat())

        try:
            return AlgoJobResult.model_validate(raw)
        except Exception as exc:
            return _failed_result(spec, started_at=started_at, message=f"结果不合契约：{exc}")


class KubernetesRunner:
    """K8s Job 执行器（生产实现）。

    本阶段不接集群。保留类与签名，让「谁负责提交 Job」这件事在代码里有明确归属，
    而不是散落成 TODO 注释。
    """

    def __init__(self, *, namespace: str, poll_interval_seconds: float = 2.0) -> None:
        self._namespace = namespace
        self._poll_interval = poll_interval_seconds

    async def run(self, spec: AlgoJobSpec) -> AlgoJobResult:
        """提交 Job 并轮询至完成。

        实现步骤（本阶段未接集群）：

        1. ``build_job_manifest(spec)`` 构造 manifest
        2. ``BatchV1Api.create_namespaced_job`` 提交
        3. 轮询 ``read_namespaced_job_status`` 直到 succeeded/failed
        4. 从 MinIO 的 ``output_prefix`` 读结果
        5. Job 由 ``ttlSecondsAfterFinished`` 自动清理
        """
        raise NotImplementedError(
            "K8s 执行器需要集群凭据；本阶段用 RDH_ALGO_RUNNER=subprocess 跑本地算子"
        )


__all__ = ["RESULT_FILENAME", "AlgoRunner", "KubernetesRunner", "SubprocessRunner"]
