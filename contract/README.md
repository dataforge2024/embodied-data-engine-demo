# robotdatahub-contract

RobotDataHub 的**单一事实来源**。所有跨模块的数据结构、事件格式、状态流转、API 规范都在这里定义，
其余 6 个模块只依赖本库，彼此不直接依赖。

当前版本 **0.1.0**。

## 内容

| 路径 | 内容 | 消费方 |
|---|---|---|
| `src/rdh_contract/enums.py` | `EpisodeStatus` / `TaskStatus` / `Role` / `JobType` / `AlgoOperator` | 全部 |
| `src/rdh_contract/state_machine.py` | Episode 状态机：合法边、终态、`can_transition()` | Platform |
| `src/rdh_contract/schemas/` | 共享数据模型（pydantic v2） | 全部 |
| `src/rdh_contract/events/` | RabbitMQ 事件 payload + `EVENT_REGISTRY` | Platform（发布）/ Scheduler（消费） |
| `src/rdh_contract/ws/protocol.py` | WebSocket 消息帧 | Agent ↔ Platform |
| `openapi/platform.yaml` | Platform REST 契约 | Tool / Agent / Scheduler |
| `deploy/` | 部署配置模板 | 运维 |

## 生成物（勿手改）

Python 是 schema 的宿主，其余格式由脚本生成并入库：

| 产物 | 生成脚本 | 消费方 |
|---|---|---|
| `events/*.json` | `scripts/export_json_schema.py` | Scheduler 做消息校验、非 Python 消费者 |
| `types/contract.ts` | `scripts/export_ts_types.py` | Platform web / Tool |

```bash
make contract-gen   # 重新生成
```

`tests/test_generated_artifacts.py` 会断言产物与源码同步——改了 schema 忘了重新生成，测试会红。

## 契约变更流程（OpenSpec）

1. **Propose** — 在 `openspec/changes/<change-name>/` 提出 proposal（需求、设计、影响范围：列出受影响的模块）
2. **Review** — 团队确认接口变更、数据模型、依赖关系
3. **Apply** — 合并契约变更 + bump 版本；各模块按 proposal 独立实现
4. **Archive** — 全部模块实现完成后归档

**版本策略**：破坏性变更（删字段、改字段类型、删状态、改 routing_key）= minor bump（0.1.0 → 0.2.0）
并且必须有对应的 OpenSpec change。新增可选字段 = patch bump。

## 被依赖方式

```toml
# 各模块的 pyproject.toml
dependencies = ["robotdatahub-contract==0.1.0"]

[tool.uv.sources]
robotdatahub-contract = { path = "../contract", editable = true }
```

`[tool.uv.sources]` 是本次单目录模拟对私有 registry 的替身。真实拆仓后删掉该节即可。

## 本地开发

```bash
make contract-test    # 测试
make contract-cov     # 覆盖率（守 80%）
make contract-lint    # ruff + mypy
```
