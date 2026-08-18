# RobotDataHub

具身智能数据采集与标注平台。本目录是**多仓库工作区的模拟**：每个子目录对应一个未来独立的代码库。

架构设计见 [`contract/docs/architecture.md`](contract/docs/architecture.md)。

## 目录 ↔ 仓库映射

| 目录 | 未来仓库 | 技术栈 | 状态 |
|---|---|---|---|
| `contract/` | `robotdatahub-contract` | Python 3.12 (pydantic v2) | **写实** |
| `platform/` | `robotdatahub-platform` | FastAPI + PG / React 18 + AntD 5 | 骨架 |
| `scheduler/` | `robotdatahub-scheduler` | Celery + RabbitMQ + K8s/KEDA | 骨架 |
| `agent/` | `robotdatahub-agent` | Python 3.12 + WebSocket + MinIO SDK | 骨架 |
| `algo/` | `robotdatahub-algo` | PyTorch + K8s Job | 骨架 |
| `tool/` | `robotdatahub-tool` | React 18 + TypeScript | 骨架 |
| `testing/` | `robotdatahub-testing` | pytest + Playwright + Locust | 骨架 |

「骨架」= 目录树 + 依赖清单 + 带完整类型签名的 stub（`raise NotImplementedError`）。业务逻辑未实现。

## 依赖铁律

**唯一允许的跨模块依赖是 `contract`。** 任何 `from platform ...` 出现在 `scheduler/` 里都是架构违规。

各模块通过包依赖引用契约，而非相对路径：

```toml
dependencies = ["robotdatahub-contract==0.1.0"]

[tool.uv.sources]
robotdatahub-contract = { path = "../contract", editable = true }
```

`[tool.uv.sources]` 是本次模拟对私有 registry 的替身。真实拆仓后删掉这一节即可，`dependencies` 一行不动。

## 契约版本

当前 `robotdatahub-contract` **0.1.0**。破坏性变更 = minor bump + 一条 OpenSpec change（流程见 `contract/openspec/project.md`）。

## 8 条核心交互速查

| # | 交互 | 方式 | 实现位置 |
|---|---|---|---|
| ① | Agent → Platform | WebSocket 长连接（心跳、任务推送、状态同步） | `agent/src/agent/ws/client.py` ↔ `platform/app/ws/manager.py` |
| ② | Agent → MinIO | 分片上传 MCAP（断点续传） | `agent/src/agent/uploader/chunked.py` |
| ③ | Agent → Platform | 上传完成 HTTP 回调 | `platform/app/api/routes/callbacks.py::upload_complete` |
| ④ | Tool ↔ Platform | HTTP REST（核验、标注） | `tool/src/api/client.ts` ↔ `platform/app/api/routes/{verification,annotation}.py` |
| ⑤ | Platform → RabbitMQ | 发布事件 | `platform/app/services/event_publisher.py` |
| ⑥ | RabbitMQ → Scheduler | 消费事件触发流水线 | `scheduler/src/scheduler/consumers/rabbit.py` |
| ⑦ | Scheduler → K8s | 创建 Job 运行 Algo 算子 | `scheduler/src/scheduler/k8s/job_builder.py` |
| ⑧ | Scheduler → Platform | 结果 HTTP 回调 | `platform/app/api/routes/callbacks.py::algo_result` |

③ 和 ⑧ 是**两个不同端点**，不要合并。

## 先看这个：跑一遍完整链路

```bash
make demo
```

8 条交互真跑一遍，输出 Episode 从 `recording` 走到 `published` 的完整状态轨迹、
算子产物统计、队列深度。约 1 秒完成，不需要任何外部服务。

## 其余命令

```bash
make contract-test      # 契约测试（166 个，守 80% 覆盖率）
make contract-gen       # 生成 events/*.json + types/contract.ts
make conformance        # 跨模块契约一致性 + 依赖铁律
make e2e                # 端到端流程测试
make check              # 全量：lint + 类型 + 测试 + 架构约束
make clean-runtime      # 清掉本地运行数据（DB / 队列 / 对象存储）
```

单模块命令见 `Makefile`。

## 本阶段范围

**不起** PostgreSQL / MinIO / RabbitMQ，**不接** K8s 集群。用同接口的本地替身：

| 生产 | 本地替身 | 保留的语义 |
|---|---|---|
| PostgreSQL | SQLite | 同一套 SQLAlchemy 模型与索引 |
| MinIO | 本地目录 | 对象键布局、分片上传、断点续传、checksum 校验 |
| RabbitMQ | 文件队列 | 按 routing_key 分队列、幂等去重、重试与死信、原子投递 |
| K8s Job | 子进程 | 环境变量注入契约、超时与失败分类；manifest 构造真实产出 |

替换点都是「协议 + 实现」分离，换实现不动调用方。**交互⑦是唯一仍为模拟的环节**
（不提交给真集群）；`scheduler/k8s/job_builder.py` 的 manifest 构造是真实的并有测试覆盖。

算法算子是**可运行的启发式实现**而非 stub：接真模型时改的只有 `Operator.process()` 内部。
