"""把事件 payload 模型导出为 JSON Schema。

产物 ``events/*.json`` 供 Scheduler 做消息校验，以及非 Python 消费者使用。
生成物入库便于 diff review；``tests/test_generated_artifacts.py`` 断言产物与源码同步。

用法::

    uv run python scripts/export_json_schema.py          # 写入
    uv run python scripts/export_json_schema.py --check   # 只校验是否同步，不写
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rdh_contract import __version__
from rdh_contract.events import EVENT_MODELS, EVENT_REGISTRY

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "events"

INDEX_FILENAME = "index.json"


def build_event_schema(routing_key: str) -> dict[str, Any]:
    """构造单个事件的 JSON Schema，附带契约元信息。"""
    model = EVENT_MODELS[routing_key]
    spec = EVENT_REGISTRY[routing_key]
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://robotdatahub.local/events/{routing_key}.json"
    schema["x-contract"] = {
        "version": __version__,
        "routing_key": spec.routing_key,
        "exchange": spec.exchange,
        "consumer_queue": spec.consumer_queue.value,
        "max_retries": spec.max_retries,
        "description": spec.description,
    }
    return schema


def build_index() -> dict[str, Any]:
    """构造事件索引，供消费方枚举全部事件。"""
    return {
        "contract_version": __version__,
        "exchange": {spec.exchange for spec in EVENT_REGISTRY.values()}.pop(),
        "events": [
            {
                "routing_key": spec.routing_key,
                "schema_file": f"{spec.routing_key}.json",
                "model": spec.model_name,
                "consumer_queue": spec.consumer_queue.value,
                "max_retries": spec.max_retries,
                "description": spec.description,
            }
            for spec in sorted(EVENT_REGISTRY.values(), key=lambda s: s.routing_key)
        ],
    }


def render_all() -> dict[str, str]:
    """生成 ``文件名 → 内容`` 映射。不落盘，便于 --check 复用。"""
    rendered = {
        f"{routing_key}.json": json.dumps(
            build_event_schema(routing_key), indent=2, ensure_ascii=False, sort_keys=True
        )
        + "\n"
        for routing_key in sorted(EVENT_MODELS)
    }
    rendered[INDEX_FILENAME] = (
        json.dumps(build_index(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    return rendered


def check(rendered: dict[str, str]) -> list[str]:
    """返回与磁盘不一致的文件清单。"""
    drifted: list[str] = []
    for name, content in rendered.items():
        path = OUTPUT_DIR / name
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            drifted.append(name)
    expected = set(rendered)
    for path in OUTPUT_DIR.glob("*.json"):
        if path.name not in expected:
            drifted.append(f"{path.name}（多余文件）")
    return sorted(drifted)


def write(rendered: dict[str, str]) -> None:
    """落盘，并清理不再需要的旧文件。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUTPUT_DIR.glob("*.json"):
        if path.name not in rendered:
            path.unlink()
    for name, content in rendered.items():
        (OUTPUT_DIR / name).write_text(content, encoding="utf-8")


def main() -> int:
    """入口。返回进程退出码。"""
    parser = argparse.ArgumentParser(description="导出事件 JSON Schema")
    parser.add_argument("--check", action="store_true", help="只校验是否同步，不写文件")
    args = parser.parse_args()

    rendered = render_all()

    if args.check:
        drifted = check(rendered)
        if drifted:
            print("JSON Schema 与源码不同步：", ", ".join(drifted), file=sys.stderr)
            print("请运行 make contract-gen", file=sys.stderr)
            return 1
        print(f"JSON Schema 已同步（{len(rendered)} 个文件）")
        return 0

    write(rendered)
    print(f"已生成 {len(rendered)} 个文件到 {OUTPUT_DIR.name}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
