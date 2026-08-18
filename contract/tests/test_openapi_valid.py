"""OpenAPI 规范校验。

``openapi/platform.yaml`` 是 Tool / Agent / Scheduler 三方共用的 REST 契约。
手写 YAML 最常见的问题是 ``$ref`` 拼错、漏错误响应、枚举与 Python 枚举漂移。
这些测试在 CI 里拦住它们。
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from rdh_contract import __version__
from rdh_contract.enums import (
    AlgoOperator,
    EpisodeStatus,
    JobStatus,
    ReviewDecision,
    Role,
    TaskStatus,
)

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi" / "platform.yaml"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    """解析后的 OpenAPI 文档。"""
    with SPEC_PATH.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, dict)
    return loaded


def iter_operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """遍历所有操作，返回 ``(path, method, operation)``。"""
    return [
        (path, method, operation)
        for path, path_item in spec["paths"].items()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    ]


def collect_refs(node: Any) -> set[str]:
    """递归收集全部 ``$ref`` 值。"""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value)
            else:
                found |= collect_refs(value)
    elif isinstance(node, list):
        for item in node:
            found |= collect_refs(item)
    return found


def resolve_ref(spec: dict[str, Any], ref: str) -> Any:
    """解析本地 ``$ref``，解析不到抛 KeyError。"""
    node: Any = spec
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


@pytest.mark.unit
class TestDocumentStructure:
    """文档基本结构。"""

    def test_spec_file_exists(self) -> None:
        """规范文件存在。"""
        assert SPEC_PATH.is_file()

    def test_openapi_version_is_3_1(self, spec: dict[str, Any]) -> None:
        """使用 OpenAPI 3.1（与 JSON Schema 2020-12 对齐）。"""
        assert spec["openapi"].startswith("3.1")

    def test_info_version_matches_contract(self, spec: dict[str, Any]) -> None:
        """``info.version`` 与契约包版本一致，避免各自漂移。"""
        assert spec["info"]["version"] == __version__

    def test_has_paths_and_schemas(self, spec: dict[str, Any]) -> None:
        """有实际内容。"""
        assert spec["paths"]
        assert spec["components"]["schemas"]

    def test_every_operation_has_summary(self, spec: dict[str, Any]) -> None:
        """每个操作都有 summary，生成的客户端方法才有可读名字。"""
        for path, method, operation in iter_operations(spec):
            assert operation.get("summary"), f"{method.upper()} {path} 缺 summary"

    def test_every_operation_has_tag(self, spec: dict[str, Any]) -> None:
        """每个操作都归入声明过的 tag。"""
        declared = {tag["name"] for tag in spec["tags"]}
        for path, method, operation in iter_operations(spec):
            tags = operation.get("tags", [])
            assert tags, f"{method.upper()} {path} 缺 tag"
            for tag in tags:
                assert tag in declared, f"{method.upper()} {path} 使用未声明的 tag：{tag}"


@pytest.mark.unit
class TestReferences:
    """引用完整性。"""

    def test_all_refs_resolve(self, spec: dict[str, Any]) -> None:
        """所有 ``$ref`` 都能解析（拼错的 ref 在运行期才炸，代价高）。"""
        unresolved = []
        for ref in sorted(collect_refs(spec)):
            assert ref.startswith("#/"), f"不支持外部引用：{ref}"
            try:
                resolve_ref(spec, ref)
            except (KeyError, TypeError):
                unresolved.append(ref)
        assert not unresolved, f"无法解析的引用：{unresolved}"

    def test_no_unused_schemas(self, spec: dict[str, Any]) -> None:
        """无未被引用的 schema（死定义会误导实现方）。"""
        refs = collect_refs(spec)
        used = {ref.rsplit("/", 1)[-1] for ref in refs}
        defined = set(spec["components"]["schemas"])
        assert not defined - used, f"未被引用的 schema：{sorted(defined - used)}"

    def test_no_unused_parameters(self, spec: dict[str, Any]) -> None:
        """无未被引用的复用参数。"""
        used = {ref.rsplit("/", 1)[-1] for ref in collect_refs(spec)}
        defined = set(spec["components"].get("parameters", {}))
        assert not defined - used, f"未被引用的 parameter：{sorted(defined - used)}"


@pytest.mark.unit
class TestErrorResponses:
    """错误响应完备性。"""

    def test_every_operation_declares_error_response(self, spec: dict[str, Any]) -> None:
        """每个操作都声明至少一个 4xx/5xx，否则客户端无从处理失败。"""
        for path, method, operation in iter_operations(spec):
            codes = [str(c) for c in operation["responses"]]
            assert any(c.startswith(("4", "5")) for c in codes), (
                f"{method.upper()} {path} 未声明任何错误响应"
            )

    def test_every_operation_declares_success_response(self, spec: dict[str, Any]) -> None:
        """每个操作都声明 2xx。"""
        for path, method, operation in iter_operations(spec):
            codes = [str(c) for c in operation["responses"]]
            assert any(c.startswith("2") for c in codes), f"{method.upper()} {path} 未声明成功响应"

    def test_authenticated_operations_declare_401(self, spec: dict[str, Any]) -> None:
        """需要鉴权的操作必须声明 401。"""
        for path, method, operation in iter_operations(spec):
            if operation.get("security") == []:
                continue  # 显式公开的端点
            codes = {str(c) for c in operation["responses"]}
            assert "401" in codes, f"{method.upper()} {path} 需鉴权却未声明 401"

    def test_mutating_operations_declare_422(self, spec: dict[str, Any]) -> None:
        """带请求体的操作必须声明 422（请求体校验失败）。"""
        for path, method, operation in iter_operations(spec):
            if "requestBody" not in operation:
                continue
            codes = {str(c) for c in operation["responses"]}
            assert "422" in codes, f"{method.upper()} {path} 有请求体却未声明 422"

    def test_state_changing_operations_declare_409(self, spec: dict[str, Any]) -> None:
        """驱动状态迁移的端点必须声明 409（非法迁移）。"""
        state_changing = (
            "/verification/{episode_id}",
            "/annotation/{episode_id}",
            "/annotation/{episode_id}/review",
            "/callbacks/upload-complete",
            "/callbacks/algo-result",
        )
        for path in state_changing:
            post = spec["paths"][path]["post"]
            assert "409" in {str(c) for c in post["responses"]}, f"POST {path} 未声明 409"


@pytest.mark.unit
class TestSecuritySchemes:
    """鉴权方案。"""

    def test_security_schemes_declared(self, spec: dict[str, Any]) -> None:
        """声明了用户 / Agent / Scheduler 三类凭据。"""
        schemes = spec["components"]["securitySchemes"]
        assert {"bearerAuth", "agentAuth", "schedulerAuth"} <= set(schemes)

    def test_all_referenced_schemes_are_declared(self, spec: dict[str, Any]) -> None:
        """操作引用的鉴权方案都已声明。"""
        declared = set(spec["components"]["securitySchemes"])
        for path, method, operation in iter_operations(spec):
            for requirement in operation.get("security", []):
                for name in requirement:
                    assert name in declared, f"{method.upper()} {path} 引用未声明的方案：{name}"

    def test_callbacks_use_dedicated_credentials(self, spec: dict[str, Any]) -> None:
        """内部回调用专用凭据而非用户 JWT —— 最小权限原则。"""
        upload = spec["paths"]["/callbacks/upload-complete"]["post"]
        algo = spec["paths"]["/callbacks/algo-result"]["post"]
        assert upload["security"] == [{"agentAuth": []}]
        assert algo["security"] == [{"schedulerAuth": []}]

    def test_login_and_health_are_public(self, spec: dict[str, Any]) -> None:
        """登录与健康检查无需鉴权。"""
        assert spec["paths"]["/auth/login"]["post"]["security"] == []
        assert spec["paths"]["/health"]["get"]["security"] == []

    def test_global_security_is_declared(self, spec: dict[str, Any]) -> None:
        """有全局默认鉴权，避免新增端点忘记加而默认公开。"""
        assert spec["security"] == [{"bearerAuth": []}]


@pytest.mark.unit
class TestEnumParity:
    """YAML 枚举与 Python 枚举必须一致 —— 这是最容易漂移的地方。"""

    @pytest.mark.parametrize(
        ("schema_name", "enum_cls"),
        [
            ("EpisodeStatus", EpisodeStatus),
            ("TaskStatus", TaskStatus),
            ("Role", Role),
            ("AlgoOperator", AlgoOperator),
            ("JobStatus", JobStatus),
            ("ReviewDecision", ReviewDecision),
        ],
    )
    def test_enum_matches_python(
        self, spec: dict[str, Any], schema_name: str, enum_cls: type[Any]
    ) -> None:
        """YAML 中的枚举取值集合与 Python 枚举一致。"""
        yaml_values = set(spec["components"]["schemas"][schema_name]["enum"])
        python_values = {member.value for member in enum_cls}
        assert yaml_values == python_values, (
            f"{schema_name} 漂移：YAML 多出 {yaml_values - python_values}，"
            f"缺少 {python_values - yaml_values}"
        )


@pytest.mark.unit
class TestCallbackSeparation:
    """交互③ 与交互⑧ 必须是两个独立端点 —— 架构文档里最容易被混为一谈的点。"""

    def test_two_distinct_callback_endpoints(self, spec: dict[str, Any]) -> None:
        """两个回调端点都存在且不同。"""
        paths = spec["paths"]
        assert "/callbacks/upload-complete" in paths
        assert "/callbacks/algo-result" in paths

    def test_callbacks_have_different_request_bodies(self, spec: dict[str, Any]) -> None:
        """两个回调的请求体 schema 不同，防止被合并成一个端点。"""

        def body_ref(path: str) -> str:
            content = spec["paths"][path]["post"]["requestBody"]["content"]
            ref = content["application/json"]["schema"]["$ref"]
            assert isinstance(ref, str)
            return ref

        assert body_ref("/callbacks/upload-complete") != body_ref("/callbacks/algo-result")

    def test_upload_callback_requires_checksum(self, spec: dict[str, Any]) -> None:
        """上传回调必须带 checksum，Platform 才能校验完整性。"""
        schema = spec["components"]["schemas"]["UploadCallback"]
        assert "checksum" in schema["required"]

    def test_algo_callback_requires_pipeline_flag(self, spec: dict[str, Any]) -> None:
        """算子回调必须带 ``pipeline_complete``，用于区分单算子完成与流水线完成。"""
        schema = spec["components"]["schemas"]["AlgoResultCallback"]
        assert "pipeline_complete" in schema["required"]
