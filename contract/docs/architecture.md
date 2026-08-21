# RobotDataHub — 架构文档

**契约版本** 0.1.0 · **最后核对** 2026-08-21（对照 HEAD 代码逐项核实）

本文描述**代码里实际存在的**架构。带「计划」字样的才是尚未落地的部分，其余均可在代码中找到对应实现。

---

## 一、系统全貌

```
       采集现场（客户网络内）                          云端
  ┌───────────────────────────┐        ┌──────────────────────────────────┐
  │                           │        │                                  │
  │   ┌───────────────┐       │   ①WS  │   ┌──────────────────────────┐   │
  │   │     Agent     │───────┼────────┼──▶│        Platform          │   │
  │   │               │◀──────┼────────┼───│                          │   │
  │   │ MCAP 录制      │       │        │   │ FastAPI + SQLAlchemy     │   │
  │   │ 分片上传       │───────┼────────┼──▶│ 状态机守卫 / 事件发布      │   │
  │   │ 断电恢复       │       │   ③回调 │   │ WS 服务端 / JWT + RBAC   │   │
  │   │ SQLite 本地状态│       │        │   └────┬───────────────┬─────┘   │
  │   └───────┬───────┘       │        │        │               │         │
  │           │ ②分片上传      │        │       ⑤发布事件    ④REST         │
  └───────────┼───────────────┘        │        │               │         │
              │                        │        ▼               │         │
              ▼                        │  ┌──────────┐          │         │
      ┌───────────────┐                │  │ RabbitMQ │          │         │
      │  对象存储      │◀───────────────┼──│ (exchange│          │         │
      │  MinIO / 本地  │                │  │  + DLX)  │          │         │
      └───────┬───────┘                │  └────┬─────┘          │         │
              ▲                        │       │ ⑥消费           │         │
              │ 读输入 / 写产物          │       ▼                │         │
              │                        │  ┌──────────────────┐  │         │
      ┌───────┴───────┐        ⑦创建Job │  │    Scheduler     │  │         │
      │  Algo 算子     │◀───────────────┼──│                  │  │         │
      │  K8s Job/子进程│                │  │ 4 类 worker      │  │         │
      │  4 个算子      │                │  │ 流水线编排        │  │         │
      └───────────────┘                │  │ Celery（执行层）  │  │         │
                                       │  └────────┬─────────┘  │         │
                                       │           │ ⑧结果回调   │         │
                                       │           └────────────▶│         │
                                       │                        ▼         │
                                       │              ┌──────────────┐    │
                                       │              │     Tool     │    │
                                       │              │ 核验/标注/审核│    │
                                       │              │  React 18    │    │
                                       │              └──────────────┘    │
                                       └──────────────────────────────────┘
```

圈号是 8 条核心交互，详见 [`interactions.md`](interactions.md)。

**一句话概括数据流**：Agent 录一条 Episode 传上来 → Scheduler 跑算子解析 → 人核验 → 系统送标 → 人标注 → 人审核 → 并入训练集。

---

## 二、七个模块

单目录工作区模拟多仓库。**唯一允许的跨模块依赖是 `contract`** —— 这条铁律由 `testing/contract_checks` 静态校验，`scheduler/` 里出现 `from app ...` 会让 `make check` 变红。

| 模块 | 职责 | 技术栈 | 代码量 | 暴露接口 |
|---|---|---|---|---|
| `contract` | 单一事实来源：数据模型、状态机、事件、WS 协议、OpenAPI | Python 3.12 + pydantic v2 | 4.5k | 被全部依赖 |
| `platform` | 核心业务、状态机守卫、事件发布、WS 服务端、Web 控制台 | FastAPI + SQLAlchemy / React 18 + AntD 5 | 10.1k | REST + WS + 事件 |
| `scheduler` | 事件消费、流水线编排、创建算子作业 | Celery + RabbitMQ + K8s | 2.0k | 无（只出不入） |
| `agent` | 采集端：录制、分片上传、断电恢复 | Python 3.12 + WebSocket | 4.6k | 无（客户网络内） |
| `algo` | 四个推理算子 | Python（计划 PyTorch）+ Docker | 0.6k | 无（被动执行） |
| `tool` | 三个人工工作台：核验、标注、审核 | React 18 + TS | 1.6k | 无（纯前端） |
| `testing` | 契约一致性 + 端到端（压测待接入） | pytest | 1.4k | 无 |

各模块的「我依赖契约的什么 / 我暴露什么 / 我参与哪几条交互」见各自 README。

### 依赖引用方式

```toml
dependencies = ["robotdatahub-contract==0.1.0"]

[tool.uv.sources]
robotdatahub-contract = { path = "../contract", editable = true }
```

`[tool.uv.sources]` 是本次单目录模拟对私有 registry 的替身。真实拆仓后删掉该节，`dependencies` 一行不动。前端同理：`@contract` 别名指向 `contract/types/contract.ts`，拆仓后改为 npm 包引用，import 语句不动。

---

## 三、契约层：为什么它是架构的核心

六个业务模块彼此不通信，只认 `contract`。它定义四类东西：

| 契约项 | 位置 | 权威性体现 |
|---|---|---|
| 枚举与数据模型 | `enums.py` / `schemas/` | Platform 的 API 模型直接用契约模型，不重写一遍 |
| Episode 状态机 | `state_machine.py` | 合法边、终态、`assert_transition()` 守卫 |
| 事件注册表 | `events/registry.py` | routing_key ↔ payload 类型 ↔ 消费队列 ↔ 重试上限 |
| WS 协议 | `ws/protocol.py` | 上行 5 帧 / 下行 6 帧，双方共用适配器解析 |

### 生成物由脚本产出，不手改

Python 是 schema 的宿主，其余格式生成并入库：

| 产物 | 生成脚本 | 消费方 |
|---|---|---|
| `events/*.json` | `scripts/export_json_schema.py` | Scheduler 消息校验、非 Python 消费者 |
| `types/contract.ts` | `scripts/export_ts_types.py` | Platform web / Tool |

`make contract-gen` 重新生成。`tests/test_generated_artifacts.py` 断言产物与源码同步 —— 改了 schema 忘了生成，测试会红。

### 契约层为何单独守 80% 覆盖率

本项目是原型，业务模块不追求覆盖率。契约层例外：它的 bug 会传播到所有下游且难以定位，而业务模块的 bug 只影响自己。状态机、事件注册表、OpenAPI 同步的测试不能省 —— 架构错位正是在这里暴露。

---

## 四、Episode 状态机

11 个状态，3 个终态。**所有状态变更必须经 `platform/app/services/episode_lifecycle.py`** —— `repositories/episode.py` 的 `apply_transition()` 不做合法性校验，绕过 lifecycle 直接调它就绕过了状态机。这一点由 `contract_checks` 静态校验。

```
  recording ──▶ uploading ──▶ uploaded ──▶ processing
      │             │             │            │
      │             │             │            ▼
      │             │             │      verification_pending ─────┐
      │             │             │            │                  │
      │             │             │            ▼                  │
      │             │             │      annotation_processing ────┤
      │             │             │            │        │         │
      │             │             │            ▼        │         │
      │             │             │      annotation_pending ◀──┐  │
      │             │             │            │              │  │
      │             │             │            ▼              │  │
      │             │             │      annotation_review ────┘  │
      │             │             │            │   │              │
      │             │             │            ▼   └──────────────┤
      │             │             │       published               │
      │             │             │      （终态）                  ▼
      └─────────────┴─────────────┴──────▶ failed          rejected
                                          （终态）          （终态）
```

完整边表（`state_machine.py` 权威）：

| 源状态 | 允许目标 |
|---|---|
| `recording` | `uploading` / `failed` |
| `uploading` | `uploaded` / `failed` |
| `uploaded` | `processing` / `failed` |
| `processing` | `verification_pending` / `failed` |
| `verification_pending` | `annotation_processing` / `rejected` |
| `annotation_processing` | `annotation_pending` / `failed` |
| `annotation_pending` | `annotation_review` / `rejected` |
| `annotation_review` | `published` / `annotation_pending` / `rejected` |
| `published` / `rejected` / `failed` | —（终态，无出边） |

### 两个容易读错的地方

**`annotation_processing` 为什么独立存在。** 质检通过后不直连 `annotation_pending`，中间加一个由 Scheduler 回调推进的送标环节。它与 `processing` 分开而不复用，因为两者回调语义不同：一个是「解析完等人看」，一个是「送标完等人标」。合成一个端点就得靠额外字段区分阶段，回调方容易传错 —— 所以 `/callbacks/algo-result` 与 `/callbacks/annotation-processing` 是两个独立端点。

**审核「退回」≠ 核验「打回」。** 前者 `annotation_review → annotation_pending`，让标注重做；后者 `→ rejected`，把 Episode 判死。UI 文案必须区分，否则操作人会误判。

### 展示层的六阶段

阶段是**展示层分组**，不改变状态机。六个阶段严格交替（人工 → 自动 → 人工 → 自动 → 人工 → 完成），看进度条即可判断下一步该谁动：

| 阶段 | 含义 | 覆盖状态 |
|---|---|---|
| 采集人工作业 | Agent 录制并分片上传 | `recording` / `uploading` / `uploaded` |
| 采集自动解析 | 算子流水线：预标注 / 质检 / 关键帧 / 异常 | `processing` |
| 采集人工质检 | 人工核验，看着自动质检报告判断可用性 | `verification_pending` |
| 标注自动送标 | 送标处理：准备标注数据 | `annotation_processing` |
| 标注人工作业 | 人工标注与标注审核 | `annotation_pending` / `annotation_review` |
| 成功 | 已发布，可进训练集 | `published` |

`failed` / `rejected` **不映射到任何阶段** —— 线性进度条无法表达「死在第几格」，界面用区别于进度条的画法呈现，并借状态流转轨迹标出中断位置。

映射表在 `platform/web/src/utils/stage.ts`，有单测守着「送标处理独占一格，不与待标注合并」这条不变量。

---

## 五、事件驱动

Platform 发布，Scheduler 消费，两者不互相 import。事件注册表是唯一权威：

| routing_key | 消费队列 | 触发时机 | 后续动作 |
|---|---|---|---|
| `episode.uploaded` | `ingest` | 上传回调落库后 | 跑四个算子 → 回调 `algo-result` |
| `annotation.processing_requested` | `tool` | 核验通过 | 送标处理 → 回调 `annotation-processing` |
| `annotation.approved` | `tool` | 审核通过 | 格式转换、并入训练集 |
| `dataset.build_requested` | `tool` | Lab 请求构建 | 产出 manifest |
| `episode.rejected` | `notify` | 核验打回 | 通知、告警 |

注意 `tool` 队列承载三个事件 —— 断言队列深度时要按增量而非总数。

### 两条纪律

**先落库再发事件。** 反过来会让 Scheduler 消费到 Platform 里还查不到的 Episode。

**重放识别为幂等而非报错。** RabbitMQ 至少一次投递，同一事件可能重复到达；目标状态已达成时视为「已处理」，返回现状而不抛异常。

### 事件出口收口

只能经 `platform/app/services/event_publisher.py`。它校验 routing_key 已注册且 payload 类型匹配 —— 错发的事件在这里被拦住，而不是让 Scheduler 反序列化失败。同样由 `contract_checks` 静态校验。

### 队列后端可切换

| 后端 | Platform 侧 | Scheduler 侧 | 用途 |
|---|---|---|---|
| `file` | `FileQueuePublisher` | `FileQueueConsumer` | 零外部依赖，`make demo` 默认 |
| `rabbit` | `RabbitPublisher` | `RabbitConsumer` | 真 broker，`make demo-rabbit` |

信封格式两者共用 `prepare_event()` / `decode_envelope()` —— 格式漂移会让切后端时静默失败。

**发布方不声明队列**是有意的：有哪些消费队列是 Scheduler 的事。`file` 后端下发布方按 `consumer_queue` 建目录，`rabbit` 后端下 Platform 只声明 exchange，投递目标由 binding 决定。

---

## 六、Scheduler 的分层

### 4 类 worker

按契约的 `consumer_queue` 自动分派，代码里不硬编码 routing_key（靠 `routing_keys_for_queue()`）：

| worker | 订阅 | 职责 |
|---|---|---|
| `ingest` | `episode.uploaded` | 解析 MCAP、抽关键帧、串算子流水线 |
| `algo` | （由 ingest 内部调度） | 创建作业跑算子 |
| `tool` | `annotation.approved` / `annotation.processing_requested` / `dataset.build_requested` | 送标处理、格式转换、训练集构建 |
| `notify` | `episode.rejected` | 回调 Platform、告警 |

三种运行形态，消费逻辑完全相同：

```
RDH_QUEUE_BACKEND=file    → worker.py         一个进程内 4 个消费循环
RDH_QUEUE_BACKEND=rabbit  → rabbit_worker.py  4 个薄消费层，翻译成 Celery 任务
生产                       → 叠加 KEDA         按队列深度伸缩 0~150 副本
```

### Celery 只管执行，不管收发领域事件

Platform 用 aio-pika 发领域事件，薄消费层校验通过后 `task.delay()`。三条理由：

1. Platform 不能是 Celery 客户端 —— 破依赖铁律
2. 契约的队列名是 KEDA 的权威来源
3. Celery protocol v2 的消息体与领域事件信封不兼容

因此**两套队列并存且必须分开**：领域事件队列名取自 `JobType`（`ingest` / `algo` / `tool` / `notify`），Celery 任务队列前缀 `celery.`。混进同一队列，消费方会把对方的消息当垃圾丢掉。

每个 task 的 `max_retries` 取自契约的事件声明，不在代码里硬编码。

### 算子执行

`k8s/job_builder.py` 的 manifest 构造是**真实的** —— TTL、GPU limits、`backoffLimit: 0`、`activeDeadlineSeconds` 都按生产设置，且有测试覆盖。只是本地不提交给集群：

```
应用层   Celery task / 消费循环
           ↓ 生产：K8s API   本地：asyncio.create_subprocess_exec
编排层   K8s Job              子进程
           ↓
执行层   Pod（GPU 节点）        本地进程
         • 读 MinIO 的 MCAP     • 读本地目录
         • 推理                 • 启发式计算
         • 产物写 MinIO         • 产物写本地
         • TTL 到期自动清理      • 进程退出
```

`backoffLimit: 0` 是刻意的：重试由 Scheduler 控制，不交给 K8s —— 我们要记录每一次失败。

四个算子**并发执行，任一失败不阻断其余**（`return_exceptions=True`）。质检算子挂了不该让预标注结果丢掉；最终 `pipeline_complete=True` 一次性回调，Platform 据 `all_succeeded` 决定进核验还是失败。

---

## 七、Algo 算子

每个算子是独立镜像，纯粹的「读输入、算、写输出」进程 —— 不碰 K8s API、不直连数据库、不调 Platform。

| 算子 | GPU | 输出字段 | 本阶段实现 | 真实实现（计划） |
|---|---|---|---|---|
| `preannotate` | 1 | `segments` | 夹爪开合状态变化点 | 时序分割网络（TCN/Transformer） |
| `quality` | 0 | `quality` | 帧元数据阈值判定 | 拉普拉斯方差 + 分割模型 |
| `keyframe` | 0 | `key_frames` | 运动能量极值 | 特征差异 / 显著性检测 |
| `anomaly` | 1 | `anomalies` | 物理约束规则 | 自编码器重构误差 |

四个都是**可运行的启发式实现而非 stub**：接真模型时改的只有各 `Operator.process()` 内部，输出契约与执行环境契约不变。

GPU 需求在 `scheduler/k8s/job_builder.py::GPU_REQUIREMENTS` 声明 —— 质检与关键帧是轻量 CV，纯 CPU 足够，不占 GPU 配额。

### 运行时契约（环境变量注入）

| 环境变量 | 含义 |
|---|---|
| `RDH_JOB_ID` | 作业 ID |
| `RDH_EPISODE_ID` | 待处理 Episode |
| `RDH_OPERATOR` | 算子类型 |
| `RDH_INPUT_PATH` | 输入 MCAP 路径（生产为 MinIO 对象键） |
| `RDH_OUTPUT_DIR` | 产物目录（生产为 MinIO 前缀） |
| `RDH_MODEL_VERSION` | 模型版本（镜像 tag） |

产物写 `$RDH_OUTPUT_DIR/result.json`，只含本算子负责的业务字段；`job_id` / `status` / 时间戳等编排字段由 Scheduler 补齐。落盘用「临时文件 + 原子 rename」，避免 Scheduler 读到写一半的 JSON。

镜像 tag 即模型版本 —— 模型版本管理就是镜像 tag 管理。

---

## 八、数据存储

### Platform 的 8 张表

| 表 | 内容 | 特性 |
|---|---|---|
| `users` | 账号、角色（JSON 数组）、密码哈希 | |
| `collect_tasks` | 采集任务、采集要求、进度计数 | |
| `episodes` | Episode 主表，含流索引/关键帧/分段/质检结果 | 状态只存当前值 |
| `episode_transitions` | 状态流转轨迹 | **只追加**，不更新不删除 |
| `algo_job_runs` | 算子运行日志 | **只追加** |
| `annotations` | 标注内容与审核轨迹 | 留修订版 |
| `agent_nodes` | Agent 注册与心跳 | |
| `datasets` | 训练集构建记录 | |

两张只追加的日志表与 `episodes` 互补：`episodes` 只存当前状态，答不了「死之前卡在哪一步」；`episode_transitions` 答「卡在哪个状态、每步停留多久」，`algo_job_runs` 答「自动环节跑了什么」。

它们都不建到 `episodes` 的 relationship —— 查询一律按 `episode_id` 走，async session 下的惰性加载只会带来 `MissingGreenlet`。

### 对象存储布局

```
episodes/<episode_id>/raw.mcap                        原始录制
episodes/<episode_id>/algo/<operator>/result.json     算子产物
episodes/<episode_id>/algo/keyframe/frames/*.jpg      抽帧图片
datasets/<dataset_id>/manifest.json                   训练集清单
```

manifest 只列**真实存在**的产物键 —— 清单里挂一个取不到的键，下游会当成损坏。

---

## 九、认证与权限

三套并行的凭据，各管一段：

| 方式 | 请求头 | 用于 | 校验位置 |
|---|---|---|---|
| JWT | `Authorization: Bearer <token>` | 人类用户（Web / Tool） | `require_roles(...)` |
| Agent 服务令牌 | `X-Agent-Token` | 交互③上传回调 | `require_agent_token` |
| Scheduler 服务令牌 | `X-Scheduler-Token` | 交互⑧等结果回调 | `require_scheduler_token` |

**服务令牌只能访问自己那个回调端点** —— Agent 的令牌打不开算子回调。

七个角色：`admin` / `recorder` / `verifier` / `annotator` / `reviewer` / `lab` / `sysops`，对应 Platform 的工作区划分。

JWT 默认 1 小时 TTL。Agent 是常驻进程，凭 username/password 自动重登（`platform.with_access_token(..., credentials=...)`）；Tool 是浏览器应用，过期回登录页即可。

### 生产启动守卫

`assert_production_ready()` 在 `environment=production` 时拒绝使用默认凭据：

```
RDH_JWT_SECRET / RDH_AGENT_TOKEN / RDH_SCHEDULER_TOKEN
（用 rabbit 后端时还有 RDH_AMQP_URL）
```

任一仍是 demo 默认值就抛错拒绝启动 —— 宁可起不来，也不带着 demo 密钥上线。

---

## 十、本地替身

不起 PostgreSQL / MinIO / K8s，用同接口的替身。替换点都是「协议 + 实现」分离，换实现不动调用方。

| 生产 | 本地替身 | 保留的语义 | 替换点 |
|---|---|---|---|
| PostgreSQL | SQLite (aiosqlite) | 同一套 SQLAlchemy 模型与索引 | `RDH_DATABASE_URL` |
| MinIO | 本地目录 | 对象键布局、分片上传、断点续传、checksum 校验 | `RDH_OBJECT_STORE_ROOT` |
| RabbitMQ | 文件队列 | 按 routing_key 分队列、幂等去重、重试与死信、原子投递 | `RDH_QUEUE_BACKEND` |
| K8s Job | 子进程 | 环境变量注入契约、超时与失败分类 | `RDH_ALGO_RUNNER` |
| 真实 MCAP（二进制） | JSON Lines | topic / timestamp / 消息体三要素 | `agent/recorder/mcap_writer.py` |
| ROS topic 订阅 | 模拟信号生成 | 夹爪开合、帧质量指标、力矩突变 | 同上 |

RabbitMQ 是唯一「要真起」的一项（`make broker-up`）—— 投递保证、死信、重试计数是 broker 的行为，文件队列只能模仿个大概。

模拟信号刻意带上真实采集的特征，让下游算子有东西可算：夹爪中段闭合再张开（预标注能切出 move/grasp/move）、相机帧带 sharpness/occlusion/motion（质检与关键帧有输入）、可选注入力矩突变（异常检测能报出来）。

### 切到 RabbitMQ 后接受的两处差异

**消费顺序在多副本下无序** —— 顺序从来不是正确性依赖，乱序事件会撞状态机守卫而非产生错误状态。

**归档留证消失** —— `file` 后端有 `processed/` 目录，RabbitMQ 下 ack 后即消失，排查靠日志与 Platform 的状态轨迹。

幂等在 RabbitMQ 下**是每个 handler 的责任**，不是基础设施保证（没有去重表）。当前实现已幂等：算子输出覆盖同名 `object_key`，回调撞状态机守卫返回 409 被当重放咽掉。新增 handler 时必须自己保证。

---

## 十一、质量保障

### 三层测试

| 层 | 位置 | 拦住什么 | 是否需要起服务 |
|---|---|---|---|
| 契约一致性 | `testing/contract_checks/` | 模块间错位：依赖铁律、版本对齐、OpenAPI vs 实现、事件接线、状态机与事件出口收口、硬编码密钥 | 否 |
| 端到端 | `testing/e2e/` | 主链路走通、打回终止、终态不可复活、重放幂等、断电恢复、坏消息进死信 | 否（in-process 驱动） |
| 压测 | `testing/load/locustfile.py` | — | 是（只有 locustfile，locust 尚未列入依赖） |

`contract_checks` 用**静态解析**而非 import 各模块 —— Testing 不该依赖它们的运行环境。这是最有价值的一层：单模块自测发现不了的错位在这里暴露。

`e2e` 用 in-process 驱动（不起 uvicorn），因此能在 CI 跑。

### 覆盖率策略

契约层守 80%（`make contract-cov`）。业务模块**不设门槛** —— 本阶段是原型，质量保障来自 `contract_checks` 的结构性校验 + `e2e` 的链路验证，这两层拦得住「模块间配合出错」，而那是本阶段最大的风险。

### 一条命令验全部

```bash
make check
```

lint + 类型检查 + 契约覆盖率 + 前端 tsc + 前端单测 + 依赖铁律 + 契约一致性 + e2e。

---

## 十二、已知不足（原型阶段接受）

**发布与落库不在一个事务里。** `mark_uploaded()` 先 `publish()`，再由路由层 `session.commit()`。RabbitMQ 下消息即时且持久，若随后 commit 失败，Scheduler 会消费到一条 Platform 里并不存在对应状态的事件。反向也成立：broker 不可达时 publish 抛异常 → commit 不发生 → 上传回调整体失败 → Agent 重试，这是安全的（无孤儿），但意味着 **broker 故障会阻塞上传链路**。生产解法是 transactional outbox。

**送标处理不跑算子。** `_prepare_annotation_data()` 是空操作 —— 四个算子在解析阶段已跑过，「送标再跑什么」尚未定义。留这个方法是为了让落点明确，接算子时不必改错误处理与回调逻辑。因此送标那条日志的 `model_version` 是写死的 `v0.1.0`，耗时也接近零。

**交互⑦仍为模拟。** manifest 构造真实且有测试，但不提交给真集群。

**数据库无迁移版本。** demo 用 `create_all`，`alembic/` 目录已就位但 `versions/` 为空。生产前需补首个迁移。

**格式转换未实装。** `annotation.approved` 触发的是 manifest 产出，lerobot / rlds 真实格式转换需单开 change。

**角色分工未强制。** 契约有 `verifier` / `annotator` / `reviewer` 三个角色，但当前 Tool 的三个工作台都用 `annotator` 令牌，四眼原则未落地 —— 前置是确定实际是否分人。

---

## 十三、相关文档

| 文档 | 内容 |
|---|---|
| [`interactions.md`](interactions.md) | 8 条核心交互的时序、载荷、失败处理 |
| [`deployment.md`](deployment.md) | 本地运行与生产部署 |
| [`episode-lifecycle.md`](episode-lifecycle.md) | Episode 生命周期细节 |
| `../openspec/project.md` | OpenSpec 变更流程 |
| 各模块 `README.md` | 「我依赖什么 / 暴露什么 / 参与哪几条交互」 |
