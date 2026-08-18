"""生成物同步性测试。

``events/*.json`` 与 ``types/contract.ts`` 由脚本从 Python 模型生成并入库。
改了 schema 却忘记重新生成，下游会拿到过期类型 —— 这些测试就是那道闸门。

同时校验生成器本身：幂等、可解析、内容与源码语义一致。
"""

import json
from pathlib import Path
from typing import Any

import pytest

import scripts.export_json_schema as json_exporter
import scripts.export_ts_types as ts_exporter
from rdh_contract import __version__
from rdh_contract.enums import EpisodeStatus
from rdh_contract.events import EVENT_MODELS, EVENT_REGISTRY
from rdh_contract.state_machine import EPISODE_TRANSITIONS, TERMINAL_STATES

CONTRACT_ROOT = Path(__file__).resolve().parent.parent
EVENTS_DIR = CONTRACT_ROOT / "events"
TS_PATH = CONTRACT_ROOT / "types" / "contract.ts"


@pytest.fixture(scope="module")
def rendered_events() -> dict[str, str]:
    """脚本当前应生成的事件 schema 内容。"""
    return json_exporter.render_all()


@pytest.fixture(scope="module")
def rendered_ts() -> str:
    """脚本当前应生成的 TS 内容。"""
    return ts_exporter.render()


@pytest.mark.unit
class TestArtifactsInSync:
    """入库产物必须与源码同步。"""

    def test_event_schemas_in_sync(self, rendered_events: dict[str, str]) -> None:
        """``events/*.json`` 与源码同步。不同步时提示重新生成。"""
        drifted = json_exporter.check(rendered_events)
        assert not drifted, f"事件 schema 不同步：{drifted}。请运行 make contract-gen"

    def test_ts_types_in_sync(self, rendered_ts: str) -> None:
        """``types/contract.ts`` 与源码同步。"""
        assert TS_PATH.is_file(), "types/contract.ts 不存在，请运行 make contract-gen"
        assert TS_PATH.read_text(encoding="utf-8") == rendered_ts, (
            "types/contract.ts 不同步，请运行 make contract-gen"
        )

    def test_no_stale_d_ts_file(self) -> None:
        """不应残留 ``contract.d.ts``：产物含运行期常量，必须是 .ts 而非 .d.ts。"""
        assert not (CONTRACT_ROOT / "types" / "contract.d.ts").exists()


@pytest.mark.unit
class TestGeneratorIdempotence:
    """生成器幂等：连续两次生成结果一致（无时间戳、无随机排序）。"""

    def test_json_generation_is_deterministic(self) -> None:
        """事件 schema 生成两次结果相同。"""
        assert json_exporter.render_all() == json_exporter.render_all()

    def test_ts_generation_is_deterministic(self) -> None:
        """TS 生成两次结果相同。"""
        assert ts_exporter.render() == ts_exporter.render()

    def test_no_timestamp_in_artifacts(self, rendered_ts: str) -> None:
        """产物不含生成时间戳，否则每次生成都产生无意义 diff。"""
        for marker in ("generated at", "Generated on", "生成时间"):
            assert marker not in rendered_ts


@pytest.mark.unit
class TestEventSchemaContent:
    """事件 schema 内容正确性。"""

    def test_one_schema_per_event(self, rendered_events: dict[str, str]) -> None:
        """每个注册事件都有一份 schema，另加一份索引。"""
        assert len(rendered_events) == len(EVENT_REGISTRY) + 1
        for routing_key in EVENT_REGISTRY:
            assert f"{routing_key}.json" in rendered_events

    def test_schemas_are_valid_json(self) -> None:
        """入库的每个文件都是合法 JSON。"""
        for path in EVENTS_DIR.glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))

    def test_schemas_declare_json_schema_dialect(self) -> None:
        """每份 schema 声明 draft 2020-12，消费方校验器才知道按哪套规则解析。"""
        for routing_key in EVENT_REGISTRY:
            schema = json.loads((EVENTS_DIR / f"{routing_key}.json").read_text(encoding="utf-8"))
            assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_schemas_carry_contract_metadata(self) -> None:
        """每份 schema 带 ``x-contract`` 元信息，含版本与消费队列。"""
        for routing_key, spec in EVENT_REGISTRY.items():
            schema = json.loads((EVENTS_DIR / f"{routing_key}.json").read_text(encoding="utf-8"))
            meta = schema["x-contract"]
            assert meta["version"] == __version__
            assert meta["routing_key"] == routing_key
            assert meta["consumer_queue"] == spec.consumer_queue.value
            assert meta["max_retries"] == spec.max_retries

    def test_schemas_require_envelope_fields(self) -> None:
        """每份 schema 要求 ``event_id`` 与 ``occurred_at``（幂等与延迟监控依赖）。"""
        for routing_key in EVENT_REGISTRY:
            schema = json.loads((EVENTS_DIR / f"{routing_key}.json").read_text(encoding="utf-8"))
            assert {"event_id", "occurred_at"} <= set(schema["required"])

    def test_index_lists_all_events(self) -> None:
        """索引文件枚举全部事件，供消费方发现。"""
        index: dict[str, Any] = json.loads((EVENTS_DIR / "index.json").read_text(encoding="utf-8"))
        assert index["contract_version"] == __version__
        listed = {entry["routing_key"] for entry in index["events"]}
        assert listed == set(EVENT_REGISTRY)

    def test_index_schema_files_exist(self) -> None:
        """索引指向的 schema 文件都真实存在。"""
        index: dict[str, Any] = json.loads((EVENTS_DIR / "index.json").read_text(encoding="utf-8"))
        for entry in index["events"]:
            assert (EVENTS_DIR / entry["schema_file"]).is_file()

    def test_no_orphan_schema_files(self) -> None:
        """无多余 schema 文件（删事件时容易漏删产物）。"""
        expected = {f"{key}.json" for key in EVENT_MODELS} | {"index.json"}
        actual = {path.name for path in EVENTS_DIR.glob("*.json")}
        assert actual == expected


@pytest.mark.unit
class TestTypeScriptContent:
    """TS 产物内容正确性。"""

    def test_declares_contract_version(self, rendered_ts: str) -> None:
        """导出契约版本，前端可在启动时比对。"""
        assert f'export const CONTRACT_VERSION = "{__version__}";' in rendered_ts

    def test_warns_against_hand_editing(self, rendered_ts: str) -> None:
        """顶部标明自动生成，避免有人手改后被下次生成覆盖。"""
        assert "请勿手改" in rendered_ts
        assert "make contract-gen" in rendered_ts

    def test_all_episode_statuses_exported(self, rendered_ts: str) -> None:
        """每个 Episode 状态都出现在 TS 联合类型中。"""
        for status in EpisodeStatus:
            assert f'"{status.value}"' in rendered_ts

    def test_state_machine_exported(self, rendered_ts: str) -> None:
        """导出状态机与判断函数，前端不必自己硬编码状态规则。"""
        assert "export const EPISODE_TRANSITIONS" in rendered_ts
        assert "export function canTransition" in rendered_ts
        assert "export function isTerminal" in rendered_ts

    def test_state_machine_has_entry_per_status(self, rendered_ts: str) -> None:
        """状态机常量为每个状态都给出条目，否则 TS 侧查表会得到 undefined。"""
        block = rendered_ts.split("export const EPISODE_TRANSITIONS", 1)[1]
        block = block.split("};", 1)[0]
        for status in EpisodeStatus:
            assert f'"{status.value}":' in block, f"TS 状态机缺少 {status.value} 条目"

    def test_terminal_states_match_python(self, rendered_ts: str) -> None:
        """TS 终态列表与 Python 一致。"""
        line = next(ln for ln in rendered_ts.splitlines() if "TERMINAL_EPISODE_STATES" in ln)
        for status in TERMINAL_STATES:
            assert f'"{status.value}"' in line
        non_terminal = set(EpisodeStatus) - TERMINAL_STATES
        for status in non_terminal:
            assert f'"{status.value}"' not in line

    def test_transition_targets_match_python(self, rendered_ts: str) -> None:
        """逐状态比对 TS 迁移目标与 Python 定义一致。"""
        block = rendered_ts.split("export const EPISODE_TRANSITIONS", 1)[1].split("};", 1)[0]
        for source, targets in EPISODE_TRANSITIONS.items():
            line = next(ln for ln in block.splitlines() if f'"{source.value}":' in ln)
            listed = {
                part.strip().strip('"')
                for part in line.split("[", 1)[1].rsplit("]", 1)[0].split(",")
                if part.strip()
            }
            assert listed == {t.value for t in targets}, f"{source.value} 的迁移目标不一致"

    def test_event_routing_keys_exported(self, rendered_ts: str) -> None:
        """导出事件 routing key 常量。"""
        assert "export type EventRoutingKey" in rendered_ts
        for routing_key in EVENT_REGISTRY:
            assert f'"{routing_key}"' in rendered_ts

    def test_key_interfaces_exported(self, rendered_ts: str) -> None:
        """跨模块共享的关键结构都导出为 interface。"""
        for name in (
            "Episode",
            "Segment",
            "Annotation",
            "CollectTask",
            "UploadCallback",
            "AlgoResultCallback",
            "User",
        ):
            assert f"export interface {name} {{" in rendered_ts

    def test_no_unknown_types_leaked(self, rendered_ts: str) -> None:
        """没有 ``unknown`` 泄漏 —— 那意味着某个字段类型没翻译成功。"""
        assert ": unknown;" not in rendered_ts

    def test_balanced_braces(self, rendered_ts: str) -> None:
        """花括号配平，粗略保证语法完整。"""
        assert rendered_ts.count("{") == rendered_ts.count("}")


@pytest.mark.unit
class TestCheckMode:
    """``--check`` 模式行为：CI 依赖它拦住不同步的提交。"""

    def test_json_check_passes_when_in_sync(self, rendered_events: dict[str, str]) -> None:
        """同步时 check 返回空列表。"""
        assert json_exporter.check(rendered_events) == []

    def test_json_check_detects_missing_file(self, rendered_events: dict[str, str]) -> None:
        """缺文件时能检测出来。"""
        mutated = dict(rendered_events)
        mutated["episode.exploded.json"] = "{}\n"
        assert "episode.exploded.json" in json_exporter.check(mutated)

    def test_json_check_detects_content_drift(self, rendered_events: dict[str, str]) -> None:
        """内容漂移时能检测出来。"""
        mutated = dict(rendered_events)
        first = next(iter(mutated))
        mutated[first] = '{"drifted": true}\n'
        assert first in json_exporter.check(mutated)
