"""把契约模型导出为 TypeScript 类型声明。

产物 ``types/contract.ts`` 供 Platform web 与 Tool 使用，两个前端共用同一份类型，
避免各写一遍 interface 后与后端漂移。

实现说明：不引入 datamodel-code-generator 等重型依赖，直接遍历 pydantic 的 JSON Schema
生成 d.ts。契约模型都是扁平结构（对象、数组、枚举、联合），手写映射足够且无额外依赖。

用法::

    uv run python scripts/export_ts_types.py          # 写入
    uv run python scripts/export_ts_types.py --check   # 只校验是否同步
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from rdh_contract import __version__
from rdh_contract.enums import (
    AlgoOperator,
    EpisodeStatus,
    JobStatus,
    JobType,
    ReviewDecision,
    Role,
    TaskStatus,
    UploadStatus,
)
from rdh_contract.events import EVENT_REGISTRY
from rdh_contract.schemas import (
    AgentHeartbeat,
    AgentNode,
    AgentTaskPush,
    AlgoJobResult,
    AlgoJobSpec,
    AlgoResultCallback,
    Annotation,
    AnnotationProcessingCallback,
    AnnotationSubmit,
    CollectTask,
    Dataset,
    Episode,
    EpisodeCreate,
    ErrorDetail,
    KeyFrame,
    LoginRequest,
    PageMeta,
    QualityReport,
    ReviewResult,
    Segment,
    SensorStream,
    TaskAssignment,
    TaskCreate,
    TaskRequirement,
    TokenResponse,
    UploadCallback,
    UploadProgress,
    User,
    VerifyResult,
)
from rdh_contract.state_machine import EPISODE_TRANSITIONS, TERMINAL_STATES
from rdh_contract.ws import ConsoleAgentStatusFrame, ConsoleUploadProgressFrame

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "types" / "contract.ts"

# 导出的枚举。TS 侧用字面量联合而非 enum，便于与 JSON 直接互操作。
ENUMS: tuple[tuple[str, type[Any]], ...] = (
    ("EpisodeStatus", EpisodeStatus),
    ("TaskStatus", TaskStatus),
    ("Role", Role),
    ("JobType", JobType),
    ("JobStatus", JobStatus),
    ("AlgoOperator", AlgoOperator),
    ("UploadStatus", UploadStatus),
    ("ReviewDecision", ReviewDecision),
)

# 导出的模型。顺序即 d.ts 中的声明顺序，按依赖与业务分组排列。
MODELS: tuple[type[BaseModel], ...] = (
    ErrorDetail,
    PageMeta,
    SensorStream,
    KeyFrame,
    Segment,
    QualityReport,
    Episode,
    EpisodeCreate,
    TaskRequirement,
    TaskAssignment,
    TaskCreate,
    CollectTask,
    VerifyResult,
    AnnotationSubmit,
    ReviewResult,
    Annotation,
    AgentHeartbeat,
    AgentTaskPush,
    AgentNode,
    UploadProgress,
    UploadCallback,
    AlgoJobSpec,
    AlgoJobResult,
    AlgoResultCallback,
    AnnotationProcessingCallback,
    Dataset,
    User,
    LoginRequest,
    TokenResponse,
    # 控制台 WS 推送帧 —— 前端订阅 /ws/console 后按 type 分派
    ConsoleAgentStatusFrame,
    ConsoleUploadProgressFrame,
)

# JSON Schema 基础类型 → TS 类型
PRIMITIVES: dict[str, str] = {
    "string": "string",
    "integer": "number",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
}


def ts_type(schema: dict[str, Any], defs: dict[str, Any]) -> str:
    """把一个 JSON Schema 节点翻译成 TS 类型表达式。"""
    if "$ref" in schema:
        ref: str = schema["$ref"]
        return ref.rsplit("/", 1)[-1]

    for key in ("anyOf", "oneOf"):
        if key in schema:
            parts = [ts_type(sub, defs) for sub in schema[key]]
            deduped = list(dict.fromkeys(parts))
            return " | ".join(deduped)

    if "const" in schema:
        return f'"{schema["const"]}"'

    if "enum" in schema:
        return " | ".join(f'"{v}"' for v in schema["enum"])

    schema_type = schema.get("type")

    if schema_type == "array":
        items = schema.get("items")
        inner = ts_type(items, defs) if items else "unknown"
        # 联合类型加括号，避免 A | B[] 的歧义
        return f"({inner})[]" if "|" in inner else f"{inner}[]"

    if schema_type == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"Record<string, {ts_type(extra, defs)}>"
        return "Record<string, unknown>"

    if isinstance(schema_type, list):
        return " | ".join(PRIMITIVES.get(t, "unknown") for t in schema_type)

    if isinstance(schema_type, str):
        if schema_type == "string" and schema.get("format") == "date-time":
            # 序列化后是 ISO 8601 字符串
            return "string"
        return PRIMITIVES.get(schema_type, "unknown")

    return "unknown"


def render_enum(name: str, enum_cls: type[Any]) -> str:
    """渲染枚举为字面量联合类型 + 取值数组。"""
    members = [m.value for m in enum_cls]
    union = "\n  | ".join(f'"{v}"' for v in members)
    values = ",\n  ".join(f'"{v}"' for v in members)
    return (
        f"export type {name} =\n  | {union};\n\n"
        f"export const {name}Values: readonly {name}[] = [\n  {values},\n];\n"
    )


def render_model(model: type[BaseModel]) -> str:
    """渲染 pydantic 模型为 TS interface。"""
    schema = model.model_json_schema(mode="serialization")
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    lines: list[str] = []

    doc = (model.__doc__ or "").strip().split("\n")[0]
    if doc:
        lines.append(f"/** {doc} */")
    lines.append(f"export interface {model.__name__} {{")

    for field_name, field_schema in schema.get("properties", {}).items():
        desc = field_schema.get("description")
        if desc:
            lines.append(f"  /** {desc} */")
        optional = "" if field_name in required else "?"
        lines.append(f"  {field_name}{optional}: {ts_type(field_schema, defs)};")

    lines.append("}")
    return "\n".join(lines) + "\n"


def render_transitions() -> str:
    """渲染状态机为 TS 常量。

    前端据此禁用非法操作按钮，而不是各自硬编码一份状态判断。
    """
    entries = []
    for source in sorted(EPISODE_TRANSITIONS, key=lambda s: s.value):
        targets = sorted(t.value for t in EPISODE_TRANSITIONS[source])
        rendered = ", ".join(f'"{t}"' for t in targets)
        entries.append(f'  "{source.value}": [{rendered}],')
    body = "\n".join(entries)
    terminal = ", ".join(f'"{s.value}"' for s in sorted(TERMINAL_STATES, key=lambda s: s.value))
    return (
        "/** Episode 合法状态迁移。权威定义在 rdh_contract.state_machine，勿手改。 */\n"
        "export const EPISODE_TRANSITIONS: Readonly<\n"
        "  Record<EpisodeStatus, readonly EpisodeStatus[]>\n"
        f"> = {{\n{body}\n}};\n\n"
        "/** 终态：无出边。 */\n"
        f"export const TERMINAL_EPISODE_STATES: readonly EpisodeStatus[] = [{terminal}];\n\n"
        "/** 判断状态迁移是否合法。前端据此禁用非法操作按钮。 */\n"
        "export function canTransition(\n"
        "  source: EpisodeStatus,\n"
        "  target: EpisodeStatus,\n"
        "): boolean {\n"
        "  return EPISODE_TRANSITIONS[source].some((s) => s === target);\n"
        "}\n\n"
        "/** 判断是否为终态。 */\n"
        "export function isTerminal(status: EpisodeStatus): boolean {\n"
        "  return TERMINAL_EPISODE_STATES.some((s) => s === status);\n"
        "}\n"
    )


def render_events() -> str:
    """渲染事件 routing_key 常量。"""
    keys = sorted(EVENT_REGISTRY)
    union = "\n  | ".join(f'"{k}"' for k in keys)
    values = ",\n  ".join(f'"{k}"' for k in keys)
    return (
        "/** RabbitMQ 事件 routing key。 */\n"
        f"export type EventRoutingKey =\n  | {union};\n\n"
        "export const EVENT_ROUTING_KEYS: readonly EventRoutingKey[] = [\n"
        f"  {values},\n"
        "];\n"
    )


def render() -> str:
    """生成完整的 d.ts 内容。"""
    blocks: list[str] = [
        "/**",
        " * RobotDataHub 契约类型 —— 自动生成，请勿手改。",
        " *",
        " * 生成命令：make contract-gen",
        " * 来源：contract/src/rdh_contract/",
        f" * 契约版本：{__version__}",
        " */",
        "",
        f'export const CONTRACT_VERSION = "{__version__}";',
        "",
        "// ---- 枚举 ----",
        "",
    ]
    blocks.extend(render_enum(name, cls) for name, cls in ENUMS)
    blocks.extend(["// ---- 状态机 ----", "", render_transitions()])
    blocks.extend(["// ---- 事件 ----", "", render_events()])
    blocks.extend(["// ---- 数据模型 ----", ""])
    blocks.extend(render_model(m) for m in MODELS)
    return "\n".join(blocks)


def main() -> int:
    """入口。返回进程退出码。"""
    parser = argparse.ArgumentParser(description="导出 TypeScript 类型声明")
    parser.add_argument("--check", action="store_true", help="只校验是否同步，不写文件")
    args = parser.parse_args()

    content = render()

    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text(encoding="utf-8") != content:
            print("types/contract.ts 与源码不同步，请运行 make contract-gen", file=sys.stderr)
            return 1
        print("types/contract.ts 已同步")
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    line_count = content.count("\n")
    print(f"已生成 {OUTPUT_PATH.name}（{line_count} 行，{len(MODELS)} 个模型）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
