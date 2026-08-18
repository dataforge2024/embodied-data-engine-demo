"""K8s Job manifest 构造（交互⑦）。

**Scheduler 是唯一持有 K8s 凭据的模块** —— Algo 算子自己不碰 K8s API，它只是被调度的进程。

即便本地用子进程执行算子，manifest 构造逻辑仍然真实产出并可断言：Job 名合法、
TTL 设置正确、GPU 资源声明存在。这样切到真集群时改的只是提交方式。
"""

from typing import Any

from rdh_contract.enums import AlgoOperator
from rdh_contract.schemas.scheduler import AlgoJobSpec

# K8s 资源名规范：小写字母数字与连字符，最长 63 字符
MAX_NAME_LENGTH = 63

NAMESPACE = "robotdatahub"

# 算子 → 是否需要 GPU。质检与关键帧是轻量 CV，纯 CPU 足够
GPU_REQUIREMENTS: dict[AlgoOperator, int] = {
    AlgoOperator.PREANNOTATE: 1,
    AlgoOperator.ANOMALY: 1,
    AlgoOperator.QUALITY: 0,
    AlgoOperator.KEYFRAME: 0,
}


def build_job_name(operator: AlgoOperator, job_id: str) -> str:
    """构造符合 K8s 命名规范的 Job 名。"""
    suffix = job_id.replace("_", "-").lower()
    name = f"algo-{operator.value}-{suffix}"
    return name[:MAX_NAME_LENGTH].rstrip("-")


def build_image(registry: str, operator: AlgoOperator, model_version: str) -> str:
    """构造镜像引用。tag 即模型版本 —— 模型版本管理就是镜像 tag 管理。"""
    return f"{registry}/algo-{operator.value}:{model_version}"


def build_job_manifest(spec: AlgoJobSpec) -> dict[str, Any]:
    """构造 K8s Job manifest。

    关键设置：

    - ``ttlSecondsAfterFinished`` —— Job 完成后自动清理，否则集群里堆满已完成 Job
    - ``backoffLimit: 0`` —— 重试由 Scheduler 控制，不交给 K8s（我们要记录每次失败）
    - ``activeDeadlineSeconds`` —— 防止算子卡死占着 GPU
    - 资源 requests == limits —— GPU 不可超卖
    """
    gpu_count = spec.gpu_count
    resources: dict[str, dict[str, str]] = {
        "requests": {"cpu": "2", "memory": "4Gi"},
        "limits": {"cpu": "4", "memory": "8Gi"},
    }
    if gpu_count > 0:
        resources["requests"]["nvidia.com/gpu"] = str(gpu_count)
        resources["limits"]["nvidia.com/gpu"] = str(gpu_count)

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": build_job_name(spec.operator, spec.job_id),
            "namespace": NAMESPACE,
            "labels": {
                "app": "robotdatahub",
                "component": "algo",
                "operator": spec.operator.value,
                "episode-id": spec.episode_id,
            },
        },
        "spec": {
            "ttlSecondsAfterFinished": spec.ttl_seconds,
            "backoffLimit": 0,
            "activeDeadlineSeconds": spec.timeout_seconds,
            "template": {
                "metadata": {"labels": {"app": "robotdatahub", "component": "algo"}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "operator",
                            "image": spec.image,
                            "env": [
                                {"name": "RDH_JOB_ID", "value": spec.job_id},
                                {"name": "RDH_EPISODE_ID", "value": spec.episode_id},
                                {"name": "RDH_OPERATOR", "value": spec.operator.value},
                                {"name": "RDH_INPUT_KEY", "value": spec.input_object_key},
                                {"name": "RDH_OUTPUT_PREFIX", "value": spec.output_prefix},
                            ],
                            "resources": resources,
                        }
                    ],
                    "nodeSelector": ({"accelerator": "nvidia"} if gpu_count > 0 else {}),
                },
            },
        },
    }


def build_spec(
    *,
    job_id: str,
    episode_id: str,
    operator: AlgoOperator,
    input_object_key: str,
    registry: str,
    model_version: str,
    timeout_seconds: int,
    ttl_seconds: int,
) -> AlgoJobSpec:
    """构造算子作业参数。"""
    return AlgoJobSpec(
        job_id=job_id,
        episode_id=episode_id,
        operator=operator,
        image=build_image(registry, operator, model_version),
        input_object_key=input_object_key,
        output_prefix=f"episodes/{episode_id}/algo/{operator.value}",
        gpu_count=GPU_REQUIREMENTS[operator],
        timeout_seconds=timeout_seconds,
        ttl_seconds=ttl_seconds,
    )


__all__ = [
    "GPU_REQUIREMENTS",
    "MAX_NAME_LENGTH",
    "NAMESPACE",
    "build_image",
    "build_job_manifest",
    "build_job_name",
    "build_spec",
]
