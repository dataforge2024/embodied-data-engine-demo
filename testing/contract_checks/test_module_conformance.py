"""跨模块契约一致性校验。

这些测试**不需要起任何服务**，它们静态检查「各模块是否真的按契约实现」：

- 依赖铁律：模块之间不得直接 import
- OpenAPI 与 Platform 实际路由一致
- 各模块声明的契约版本一致
- 事件的发布方与消费方成对存在

这是 Testing 模块最有价值的部分：单模块自测无法发现的错位，在这里暴露。
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from rdh_contract import __version__ as contract_version
from rdh_contract.enums import JobType
from rdh_contract.events import EVENT_REGISTRY, routing_keys_for_queue

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PYTHON_MODULES = ("platform", "scheduler", "agent", "algo")
ALL_MODULES = (*PYTHON_MODULES, "contract", "tool", "testing")

# 各模块的顶层 Python 包名 → 不得被其他模块直接 import
MODULE_PACKAGES = {
    "platform": "app",
    "scheduler": "scheduler",
    "agent": "agent",
    "algo": "algo_common",
}


def source_files(module: str) -> list[Path]:
    """某模块的全部 Python 源文件。"""
    root = REPO_ROOT / module
    return [
        p for p in root.rglob("*.py") if ".venv" not in p.parts and "__pycache__" not in p.parts
    ]


@pytest.mark.contract
class TestDependencyDiscipline:
    """依赖铁律：唯一允许的跨模块依赖是 contract。"""

    @pytest.mark.parametrize("module", PYTHON_MODULES)
    def test_module_does_not_import_siblings(self, module: str) -> None:
        """模块不得直接 import 其他业务模块的包。"""
        own_package = MODULE_PACKAGES[module]
        forbidden = {package for name, package in MODULE_PACKAGES.items() if package != own_package}
        violations: list[str] = []

        for path in source_files(module):
            text = path.read_text(encoding="utf-8")
            for package in forbidden:
                pattern = rf"^\s*(?:from|import)\s+{re.escape(package)}(?:\.|\s|$)"
                if re.search(pattern, text, re.MULTILINE):
                    violations.append(f"{path.relative_to(REPO_ROOT)} → {package}")

        assert not violations, f"{module} 违反依赖铁律：{violations}"

    def test_contract_does_not_depend_on_business_modules(self) -> None:
        """contract 是底座，不得反向依赖任何业务模块。"""
        violations: list[str] = []
        for path in source_files("contract"):
            text = path.read_text(encoding="utf-8")
            for package in MODULE_PACKAGES.values():
                if re.search(rf"^\s*(?:from|import)\s+{re.escape(package)}\b", text, re.MULTILINE):
                    violations.append(f"{path.relative_to(REPO_ROOT)} → {package}")
        assert not violations, f"contract 反向依赖业务模块：{violations}"

    @pytest.mark.parametrize("module", PYTHON_MODULES)
    def test_module_declares_contract_dependency(self, module: str) -> None:
        """每个 Python 模块都在 pyproject 里声明对 contract 的依赖。"""
        config = tomllib.loads((REPO_ROOT / module / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = config["project"]["dependencies"]
        assert any("robotdatahub-contract" in d for d in dependencies), (
            f"{module} 未声明 contract 依赖"
        )

    @pytest.mark.parametrize("module", PYTHON_MODULES)
    def test_contract_dependency_is_pinned(self, module: str) -> None:
        """contract 依赖必须钉版本 —— 契约漂移是最难排查的故障。"""
        config = tomllib.loads((REPO_ROOT / module / "pyproject.toml").read_text(encoding="utf-8"))
        entry = next(d for d in config["project"]["dependencies"] if "robotdatahub-contract" in d)
        assert "==" in entry, f"{module} 的 contract 依赖未钉版本：{entry}"

    @pytest.mark.parametrize("module", PYTHON_MODULES)
    def test_no_cross_directory_path_injection(self, module: str) -> None:
        """不得用 sys.path 注入绕过包依赖。"""
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in source_files(module)
            if re.search(r"sys\.path.*\.\./", path.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"{module} 存在跨目录 sys.path 注入：{offenders}"


@pytest.mark.contract
class TestVersionAlignment:
    """契约版本对齐。"""

    @pytest.mark.parametrize("module", PYTHON_MODULES)
    def test_pinned_version_matches_contract(self, module: str) -> None:
        """各模块钉的版本号与 contract 实际版本一致。"""
        config = tomllib.loads((REPO_ROOT / module / "pyproject.toml").read_text(encoding="utf-8"))
        entry = next(d for d in config["project"]["dependencies"] if "robotdatahub-contract" in d)
        pinned = entry.split("==", 1)[1].strip()
        assert pinned == contract_version, (
            f"{module} 钉的契约版本 {pinned} 与实际 {contract_version} 不一致"
        )

    def test_openapi_version_matches_contract(self) -> None:
        """OpenAPI 的 info.version 与契约版本一致。"""
        spec = yaml.safe_load(
            (REPO_ROOT / "contract" / "openapi" / "platform.yaml").read_text(encoding="utf-8")
        )
        assert spec["info"]["version"] == contract_version

    def test_generated_ts_declares_same_version(self) -> None:
        """生成的 TS 产物版本与契约一致。"""
        content = (REPO_ROOT / "contract" / "types" / "contract.ts").read_text(encoding="utf-8")
        assert f'export const CONTRACT_VERSION = "{contract_version}";' in content


@pytest.mark.contract
class TestOpenApiMatchesImplementation:
    """OpenAPI 规范与 Platform 实际路由一致。

    规范里写了但没实现，或实现了但没写进规范，都是契约事故。
    """

    @staticmethod
    def _spec_paths() -> set[str]:
        spec = yaml.safe_load(
            (REPO_ROOT / "contract" / "openapi" / "platform.yaml").read_text(encoding="utf-8")
        )
        return set(spec["paths"])

    @staticmethod
    def _implemented_paths() -> set[str]:
        """从 Platform 的路由定义里提取路径。

        用静态解析而非 import Platform —— Testing 不该依赖 Platform 的运行环境。

        一个文件里可能定义多个 router（如 review.py 的 verification_router 与
        annotation_router），因此要按变量名分别记录各自的 prefix。
        """
        routes_dir = REPO_ROOT / "platform" / "app" / "api" / "routes"
        paths: set[str] = set()
        for path in routes_dir.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            prefixes = {
                name: prefix
                for name, prefix in re.findall(r'(\w+)\s*=\s*APIRouter\(\s*prefix="([^"]*)"', text)
            }
            # 无 prefix 的 router 也要登记
            for name in re.findall(r"(\w+)\s*=\s*APIRouter\(", text):
                prefixes.setdefault(name, "")

            for router_name, _method, route in re.findall(
                r'@(\w+)\.(get|post|put|patch|delete)\(\s*"([^"]*)"', text
            ):
                prefix = prefixes.get(router_name, "")
                paths.add(f"{prefix}{route}" or "/")
        return paths

    def test_every_spec_path_is_implemented(self) -> None:
        """规范里的每个路径都有实现。"""
        missing = self._spec_paths() - self._implemented_paths()
        assert not missing, f"规范声明但 Platform 未实现：{sorted(missing)}"

    def test_callback_endpoints_both_exist(self) -> None:
        """交互③ 与 交互⑧ 的端点都存在且分开。"""
        implemented = self._implemented_paths()
        assert "/callbacks/upload-complete" in implemented
        assert "/callbacks/algo-result" in implemented


@pytest.mark.contract
class TestEventWiring:
    """事件的发布方与消费方成对存在。"""

    def test_every_event_has_a_consumer_queue(self) -> None:
        """每个事件都归属某个 worker 队列。"""
        covered: set[str] = set()
        for queue in JobType:
            covered |= set(routing_keys_for_queue(queue))
        assert covered == set(EVENT_REGISTRY)

    def test_platform_publishes_through_single_exit(self) -> None:
        """Platform 只在 event_publisher 里做实际投递。

        其他地方若直接写队列目录，就绕过了契约校验。
        """
        platform_app = REPO_ROOT / "platform" / "app"
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in platform_app.rglob("*.py")
            if path.name != "event_publisher.py"
            and re.search(r"event_queue_dir\s*/", path.read_text(encoding="utf-8"))
        ]
        assert not offenders, f"绕过 event_publisher 直接写队列：{offenders}"

    def test_scheduler_consumes_via_registry(self) -> None:
        """Scheduler 通过契约注册表反序列化，不硬编码 routing_key 到模型的映射。

        解码收口在 ``consumers/event.py``（两个后端共用），重试上限各后端自己按
        ``get_spec`` 取 —— 所以按整个 consumers 包检查，而不是盯单个文件。
        """
        consumers = REPO_ROOT / "scheduler" / "src" / "scheduler" / "consumers"
        sources = {path.name: path.read_text(encoding="utf-8") for path in consumers.glob("*.py")}

        assert any("get_model" in text for text in sources.values()), (
            "Scheduler 未使用契约的 get_model"
        )
        for backend in ("queue.py", "rabbit.py"):
            assert "get_spec" in sources[backend], f"{backend} 未按契约取 max_retries"

    def test_scheduler_binds_queues_from_contract(self) -> None:
        """RabbitMQ 的队列绑定取自 ``routing_keys_for_queue()``，不手写 binding key。

        绑定漂移会让事件静默无人消费 —— 队列在、消息进 exchange，但没有绑定接住。
        """
        rabbit = (
            REPO_ROOT / "scheduler" / "src" / "scheduler" / "consumers" / "rabbit.py"
        ).read_text(encoding="utf-8")
        assert "routing_keys_for_queue" in rabbit, "RabbitConsumer 未按契约取绑定 key"

    def test_consumers_do_not_hardcode_routing_keys(self) -> None:
        """消费层不出现 routing_key 字面量 —— 全部经注册表查表。"""
        consumers = REPO_ROOT / "scheduler" / "src" / "scheduler" / "consumers"
        offenders: list[str] = []
        for path in consumers.glob("*.py"):
            for routing_key in EVENT_REGISTRY:
                if f'"{routing_key}"' in path.read_text(encoding="utf-8"):
                    offenders.append(f"{path.name} 硬编码 {routing_key}")
        assert not offenders, offenders

    def test_celery_tasks_cover_every_event(self) -> None:
        """每个注册事件都有对应的 Celery task —— 否则新事件进队列后无人处理。"""
        celery_app = (REPO_ROOT / "scheduler" / "src" / "scheduler" / "celery_app.py").read_text(
            encoding="utf-8"
        )
        missing = [key for key in EVENT_REGISTRY if f'"{key}"' not in celery_app]
        assert not missing, f"以下事件没有 Celery task 映射：{missing}"


@pytest.mark.contract
class TestStateMachineDiscipline:
    """状态机收口。"""

    def test_only_lifecycle_service_applies_transitions(self) -> None:
        """只有 episode_lifecycle 调用仓储的 apply_transition。"""
        platform_app = REPO_ROOT / "platform" / "app"
        callers = [
            str(path.relative_to(REPO_ROOT))
            for path in platform_app.rglob("*.py")
            if "apply_transition" in path.read_text(encoding="utf-8")
            and path.name not in {"episode_lifecycle.py", "episode.py"}
        ]
        assert not callers, f"绕过 lifecycle 直接改状态：{callers}"

    def test_lifecycle_uses_contract_guard(self) -> None:
        """lifecycle 确实调用了契约的守卫函数。"""
        source = (REPO_ROOT / "platform" / "app" / "services" / "episode_lifecycle.py").read_text(
            encoding="utf-8"
        )
        assert "assert_transition" in source, "lifecycle 未使用契约守卫"


@pytest.mark.contract
class TestModuleStructure:
    """模块结构完整性。"""

    @pytest.mark.parametrize("module", ALL_MODULES)
    def test_module_has_readme(self, module: str) -> None:
        """每个模块都有 README。"""
        assert (REPO_ROOT / module / "README.md").is_file(), f"{module} 缺少 README"

    @pytest.mark.parametrize("module", ("platform", "scheduler", "agent", "algo", "tool"))
    def test_readme_documents_interactions(self, module: str) -> None:
        """README 说明本模块参与哪几条交互 —— 便于对照架构文档排查断点。"""
        content = (REPO_ROOT / module / "README.md").read_text(encoding="utf-8")
        assert "交互" in content, f"{module} 的 README 未说明参与的交互"

    def test_no_secrets_in_committed_files(self) -> None:
        """源码里没有硬编码的真实密钥。

        本地 demo 的默认值带 ``local-`` 前缀且在生产启动时被拒绝，不算密钥。
        """
        pattern = re.compile(
            r"(?i)(secret|token|password)\s*[:=]\s*['\"](?!local-|\{|\$|changeme)[^'\"]{16,}['\"]"
        )
        offenders: list[str] = []
        for module in PYTHON_MODULES:
            for path in source_files(module):
                for match in pattern.finditer(path.read_text(encoding="utf-8")):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:60]}")
        assert not offenders, f"疑似硬编码密钥：{offenders}"
