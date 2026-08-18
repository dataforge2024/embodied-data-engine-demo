# RobotDataHub 项目级约定

> 所有 OpenSpec change 都参照本文件。记录「系统由哪些模块组成、各模块边界、契约层内容、推进顺序」。
> 架构全貌见 `contract/docs/architecture.md`；状态机细节见 `contract/docs/episode-lifecycle.md`。

## 一句话定位

RobotDataHub 是具身智能数据的**采集—处理—标注—发布**流水线：采集 PC 端录制 MCAP 上传对象存储，
云端解析索引后经算法预标注，人工核验与标注后产出训练集。

## 模块全景

```
┌──────────────────── contract（契约底座，全部模块依赖）────────────────────┐
│  · enums          EpisodeStatus / TaskStatus / Role / JobType / AlgoOperator │
│  · state_machine  Episode 9 态流转的唯一事实来源                             │
│  · schemas        Episode / Segment / Task / Annotation / Agent / User       │
│  · events         RabbitMQ payload + EVENT_REGISTRY（routing_key → model）   │
│  · ws/protocol    Agent ↔ Platform WebSocket 帧定义                          │
│  · openapi        Platform REST 契约                                         │
└──────────────────────────────────────────────────────────────────────────────┘

业务模块（各自独立代码库，彼此不直接依赖）
┌────────────┬──────────────────────────────────────────────────────────┐
│ Platform ★ │ 核心业务：任务/采集/核验/标注/训练集/监控 + WS 服务 + 事件发布 │
│ Scheduler  │ 调度底座：消费事件，编排 4 类 worker，创建 K8s Job          │
│ Agent      │ 采集 PC 端：MCAP 录制、分片上传、断点续传、断电恢复          │
│ Algo       │ GPU 算子：预标注 / 质检 / 关键帧 / 异常检测（K8s Job）       │
│ Tool       │ 质检标注前端：多视角回放、时间轴编辑、分段描述               │
│ Testing    │ 横向质量保障：集成 / E2E / 压测 / 契约一致性                 │
└────────────┴──────────────────────────────────────────────────────────┘
```

## 模块边界（谁能碰什么）

| 关注点 | 唯一负责方 | 说明 |
|---|---|---|
| Episode 状态变更 | Platform `services/episode_lifecycle.py` | 必须过 `can_transition()` 守卫；repository 不暴露裸 status 赋值 |
| RabbitMQ 发布 | Platform `services/event_publisher.py` | 唯一出口，payload 必须是 contract 的事件模型 |
| RabbitMQ 消费 | Scheduler `consumers/rabbit.py` | 按 `EVENT_REGISTRY` 路由到 Celery task |
| K8s Job 创建 | Scheduler `k8s/job_builder.py` | 唯一有 K8s 凭据的地方；Algo 自己不碰 K8s API |
| MinIO 写入（原始 MCAP） | Agent `uploader/` | 分片上传 |
| MinIO 读写（算子产物） | Algo `algo_common/io.py` | 算子不直接构造 client |
| 数据库 schema | Platform `alembic/` | Scheduler 只经 HTTP 回调改数据，不直连 PG |
| 契约定义 | contract | 其余模块只读 |

**铁律**：任何模块不得直接 import 另一业务模块。`make arch-check` 强制校验。

## 契约层内容与变更影响面

| 契约项 | 改动影响的模块 |
|---|---|
| `EpisodeStatus` / `state_machine` | Platform、Tool（状态展示）、Testing |
| `schemas/episode.py` (Segment/KeyFrame) | Platform、Tool、Algo、Scheduler |
| `events/` (payload 或 routing_key) | Platform、Scheduler |
| `ws/protocol.py` | Agent、Platform |
| `openapi/platform.yaml` | Tool、Agent、Scheduler |

提 change 时必须在 proposal 里列出受影响模块清单。

## 推进顺序

1. **契约落地**（当前）— contract 写实并测试通过，6 模块骨架就位
2. **主干贯通** — Agent 录制上传 → Platform 接收 → RabbitMQ → Scheduler → Algo stub → 回调
3. **人工环节** — Tool 核验与标注前端 + Platform 核验/标注队列
4. **算子实装** — 4 个 Algo 算子接真模型
5. **训练集与运维** — Lab 训练集构建 + SysOps 监控 + KEDA 弹性
6. **质量收口** — Testing 全面覆盖，80% 覆盖率达标

## 开发要求

- **TDD**：先写测试再写实现；覆盖率 ≥ 80%，关键路径 100%
- **Lint**：Python 用 ruff + mypy（`disallow_untyped_defs`），前端用 prettier + tsc
- **不可变**：数据模型全部 frozen，状态变更返回新对象而非原地修改
- **安全**：无硬编码密钥、边界处校验输入、错误信息不泄露内部细节
- **AI 辅助**：架构设计与安全审查由人主导，AI 生成代码必须人工 Review
