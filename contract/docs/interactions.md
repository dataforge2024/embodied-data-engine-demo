# RobotDataHub — 模块间交互文档

**契约版本** 0.1.0 · **最后核对** 2026-08-21（对照 HEAD 代码逐项核实）

八条核心交互的载荷、触发条件、失败处理。架构总览见 [`architecture.md`](architecture.md)。

---

## 速查表

| # | 交互 | 方式 | 发起方 → 接收方 | 凭据 | 实现位置 |
|---|---|---|---|---|---|
| ① | WS 长连接 | WebSocket | Agent → Platform | 首帧 `up.register` | `agent/ws/client.py` ↔ `platform/ws/manager.py` |
| ② | 分片上传 | 对象存储 SDK | Agent → MinIO | 上传凭据 | `agent/uploader/chunked.py` |
| ③ | 上传完成回调 | HTTP POST | Agent → Platform | `X-Agent-Token` | `platform/api/routes/callbacks.py::upload_complete` |
| ④ | 人工环节 REST | HTTP | Tool → Platform | JWT Bearer | `tool/api/client.ts` ↔ `platform/api/routes/review.py` |
| ⑤ | 发布事件 | AMQP / 文件队列 | Platform → 队列 | broker 凭据 | `platform/services/event_publisher.py` |
| ⑥ | 消费事件 | AMQP / 文件队列 | 队列 → Scheduler | broker 凭据 | `scheduler/consumers/queue.py`、`rabbit.py` |
| ⑦ | 创建算子作业 | K8s API / 子进程 | Scheduler → Algo | K8s 凭据 | `scheduler/k8s/job_builder.py`、`runner.py` |
| ⑧ | 结果回调 | HTTP POST | Scheduler → Platform | `X-Scheduler-Token` | `platform/api/routes/callbacks.py` |

**③ 和 ⑧ 是两个不同端点，不要合并** —— 调用方、凭据、语义、驱动的状态迁移都不同。

---

## 主链路时序

一条 Episode 从录制到发布，八条交互的先后顺序：

```
Agent          Platform         队列        Scheduler        Algo         Tool
  │                │              │            │              │            │
  │──① 注册/心跳──▶│              │            │              │            │
  │                │              │            │              │            │
  │═══② 分片上传 ══════▶ 对象存储 │            │              │            │
  │                │              │            │              │            │
  │──③ 上传回调───▶│              │            │              │            │
  │                │ uploading    │            │              │            │
  │                │ → uploaded   │            │              │            │
  │                │──⑤ episode.uploaded ─────▶│              │            │
  │                │ → processing │            │              │            │
  │                │              │──⑥ 消费──▶│              │            │
  │                │              │            │──⑦ 跑 4 算子▶│            │
  │                │              │            │◀── 产物 ─────│            │
  │                │◀────── ⑧ algo-result ─────│              │            │
  │                │ → verification_pending    │              │            │
  │                │              │            │              │            │
  │                │◀───────────── ④ 取核验队列 ────────────────────────────│
  │                │◀───────────── ④ 提交核验（通过）──────────────────────│
  │                │ → annotation_processing   │              │            │
  │                │──⑤ annotation.processing_requested ─────▶│            │
  │                │              │──⑥ 消费──▶│              │            │
  │                │◀── ⑧ annotation-processing ──│           │            │
  │                │ → annotation_pending      │              │            │
  │                │              │            │              │            │
  │                │◀───────────── ④ 提交标注 ──────────────────────────────│
  │                │ → annotation_review       │              │            │
  │                │◀───────────── ④ 提交审核（通过）──────────────────────│
  │                │ → published  │            │              │            │
  │                │──⑤ annotation.approved ──▶│              │            │
  │                │              │──⑥ 消费──▶│ 产出 manifest │            │
```

人工环节有三处（核验、标注、审核），自动环节有两处（解析、送标）—— 严格交替。

---

## ① Agent ↔ Platform：WebSocket 长连接

**端点** `ws://<host>/api/v1/ws/agent`

**首帧必须是 `up.register`** —— 服务端据此建立 `agent_id` 与连接的映射，之后才接受其他帧。

### 帧类型

上行 5 种：

| 帧 | 内容 | 时机 |
|---|---|---|
| `up.register` | `agent_id` / `hostname` / `version` | 连上后第一帧 |
| `up.heartbeat` | 磁盘、CPU、录制状态 | 周期性 |
| `up.episode_status` | Episode 状态变更 | 状态变化时 |
| `up.upload_progress` | 已传分片数 / 总分片数 | 上传中 |
| `up.ack` | 对下行帧的确认 | 收到需确认的下行帧 |

下行 6 种：

| 帧 | 内容 | 时机 |
|---|---|---|
| `down.registered` | 注册确认 | 收到 `up.register` 后 |
| `down.task_push` | 任务下发 | 管理员分派任务 |
| `down.task_cancel` | 任务取消 | 管理员取消 |
| `down.upload_grant` | 上传凭据 | Agent 请求上传 |
| `down.upload_trigger` | 触发上传 | 管理员手动触发 |
| `down.error` | 协议或业务错误 | 帧不合契约等 |

双方共用契约的适配器解析（Platform 用 `UPSTREAM_ADAPTER`，Agent 用 `DOWNSTREAM_ADAPTER`）—— 不各写一份 parser。

### 心跳与离线判定

`RDH_HEARTBEAT_TIMEOUT_SECONDS`（默认 45s）内无心跳则判离线。

### 断线重连

指数退避，`RDH_RECONNECT_INITIAL_SECONDS`（1s）起，上限 `RDH_RECONNECT_MAX_SECONDS`（30s）。重连后重新走 `up.register`。

### 另有一条控制台 WS

`ws://<host>/api/v1/ws/console` —— Platform Web 用它接实时帧（Agent 状态、上传进度）。当前只广播这两类。

### 已知不足

Agent 不回 `up.ack`，`down.task_cancel` 零处理，`up.upload_progress` 服务端读不到。三处都在待办清单里，与主链路无依赖。

---

## ② Agent → 对象存储：分片上传

**分片大小** `RDH_CHUNK_SIZE_BYTES`，默认 256 KiB
**单片重试** `RDH_MAX_UPLOAD_RETRIES`，默认 3

### 断电恢复是这条交互的核心

**每一步先落 SQLite 再执行。** 分片粒度落库（`mark_part_uploaded` 每片一次），因此进程被杀最多重传一片。

启动时 `recovery.py` 扫两类残局：

| 残局 | 症状 | 处理 |
|---|---|---|
| 上传没传完 | `upload_status != completed` | 读 `uploaded_parts` **只补缺口**，不重传已完成分片 |
| 传完但回调没成功 | `upload_status = completed AND callback_done = 0` | 补发交互③的回调 |

第二类最容易被忽略：文件已经在对象存储里，但 Platform 不知道，Episode 会永远卡在 `uploading`。恢复时必须把它也捞出来。

续传逻辑本身**不是替身** —— 换 MinIO 时改的只有 `_write_part` 与 `complete`。

---

## ③ Agent → Platform：上传完成回调

**端点** `POST /api/v1/callbacks/upload-complete`
**凭据** `X-Agent-Token`

### 载荷

```
episode_id       Episode ID
object_key       对象键
size_bytes       文件大小
checksum         SHA-256
duration_ms      采集时长
recorded_topics  实际录制到的 topic 列表
completed_at     上传完成时间（UTC）
```

### 服务端独立重算 checksum

**不信任 Agent 的声明** —— 传输截断或磁盘错误都会导致文件损坏，而 Agent 自己算的值无法证明落盘内容正确。不符则 422。

### 驱动的状态迁移

```
uploading → uploaded → processing
```

推到 `processing` 而不停在 `uploaded` 是刻意的：事件已投递即视为进入处理。若停在 `uploaded`，Scheduler 回调 `algo-result` 时 `processing → verification_pending` 会因当前仍是 `uploaded` 而非法（409），整条解析链路静默卡死。

### 重放识别

Agent 恢复流程会补发本回调。判定「已处理」的条件是**当前状态不是 `uploading`** —— 涵盖 `uploaded` 及其之后的所有状态。只认 `uploaded` 会让补发撞上非法迁移。重放返回 200 而非 409。

---

## ④ Tool ↔ Platform：人工环节 REST

**凭据** JWT Bearer。三个工作台的端点都要求 `annotator` 角色（四眼原则未落地，见架构文档「已知不足」）。

### 端点

| 环节 | 取队列 | 提交 | 状态迁移 |
|---|---|---|---|
| 核验 | `GET /verification/queue` | `POST /verification/{episode_id}` | `verification_pending` → `annotation_processing` / `rejected` |
| 标注 | `GET /annotation/queue` | `POST /annotation/{episode_id}` | `annotation_pending` → `annotation_review` |
| 审核 | `GET /annotation/review-queue` | `POST /annotation/{episode_id}/review` | `annotation_review` → `published` / `annotation_pending` |

辅助端点：`GET /episodes/{id}`（详情）、`GET /annotation/{episode_id}`（已有标注）。

### 三条纪律

**路径参数与请求体的 `episode_id` 不一致时报 422** —— 防止误操作改错 Episode。

**标注不传提交人。** `annotated_by` 由 Platform 从 JWT 取；客户端传 user_id 反而给了伪造空间。核验与审核不同 —— 契约的 `VerifyResult` / `ReviewResult` 把操作人放在请求体里，得由前端填。

**前置检查用 `assert_actionable`。** Episode 不在该操作要求的状态上时抛 `UnexpectedStatusError` → 409 `UNEXPECTED_EPISODE_STATUS`。这与状态机的 `INVALID_STATE_TRANSITION` 分开：前者是「当前不是这一步」（该刷新），后者是「这条边不存在」（流程不允许）。

### 深链

Platform Web 的「去核验 / 去标注 / 去审核」按状态给出入口，跳 `<tool>/?episode=<id>&stage=<verify|annotate|review>`。只有等人操作的三个状态给链接 —— 其余状态要么在自动流水线里，要么已到终态。

Tool 与 Platform Web 是**两个独立前端、不同 origin**，各自的 localStorage 互不可见。Tool 用 demo 凭据自动登录，不共享 Platform Web 的会话。

---

## ⑤ Platform → 队列：发布事件

**唯一出口** `platform/app/services/event_publisher.py`。它校验 routing_key 已注册且 payload 类型匹配 —— 错发的事件在这里被拦住，而不是让 Scheduler 反序列化失败。绕过它直接写队列目录由 `contract_checks` 静态拦住。

### 五个事件

| routing_key | 消费队列 | 触发点 |
|---|---|---|
| `episode.uploaded` | `ingest` | `mark_uploaded()` 落库后 |
| `annotation.processing_requested` | `tool` | `request_annotation_processing()`，核验通过 |
| `annotation.approved` | `tool` | `publish_episode()`，审核通过 |
| `dataset.build_requested` | `tool` | Lab 请求构建训练集 |
| `episode.rejected` | `notify` | `reject()`，核验打回 |

**`tool` 队列承载三个事件** —— 测试里断言队列深度要按增量，不能写死总数。

### 信封格式

`prepare_event()` 产出，两个后端共用。Scheduler 用 `decode_envelope()` 按同一结构解析 —— 格式漂移会让切后端时静默失败。

### 先落库再发事件

反过来会让 Scheduler 消费到 Platform 里还查不到的 Episode。

### 发布方不声明队列

有哪些消费队列是 Scheduler 的事，发布方不该知道：

| 后端 | 投递目标由谁决定 |
|---|---|
| `file` | 发布方按 `consumer_queue` 建目录 |
| `rabbit` | binding —— Platform 只声明 exchange |

### 已知不足

发布与落库不在一个事务里（详见架构文档）。生产解法是 transactional outbox。

---

## ⑥ 队列 → Scheduler：消费事件

**按契约分派，代码里不硬编码 routing_key** —— `routing_keys_for_queue()` 决定各 worker 订阅什么。

### 两个实现，三个动作同形

| 后端 | 实现 | 死信 | 归档 |
|---|---|---|---|
| `file` | `consumers/queue.py::FileQueueConsumer` | `dlq/` 目录 | `processed/` 目录 |
| `rabbit` | `consumers/rabbit.py::RabbitConsumer` | `EXCHANGE_DLX` | 无（ack 后即消失） |

`fetch` / `ack` / `reject` 三个动作在两个后端下同形。

### 文件队列保留的消费语义

不是玩具替身，以下性质与 RabbitMQ 一致：

- **幂等去重** —— 按 `event_id` 记录已处理集合，重放跳过
- **重试与死信** —— 重试到契约声明的 `max_retries`，耗尽进 `dlq/`
- **消费顺序** —— 文件名带时间戳前缀
- **原子投递** —— 发布方临时文件 + rename，消费方读不到半个文件

### 无人处理的事件进死信

不 ack 掉 —— 静默丢弃会让问题无从发现。

### 切到 RabbitMQ 后的两处差异

**消费顺序在多副本下无序。** 顺序从来不是正确性依赖 —— 乱序事件会撞状态机守卫而非产生错误状态。

**归档留证消失。** 排查靠日志与 Platform 的状态轨迹。

**幂等成为每个 handler 的责任**，不是基础设施保证（没有去重表）。当前实现已幂等：算子输出覆盖同名 `object_key`，回调撞状态机守卫返回 409 被当重放咽掉。新增 handler 时必须自己保证。

---

## ⑦ Scheduler → Algo：创建算子作业

**Scheduler 是唯一持有 K8s 凭据的模块** —— Algo 算子自己不碰 K8s API，它只是被调度的进程。

### 四个算子并发跑

`PIPELINE_OPERATORS` = quality / keyframe / preannotate / anomaly。顺序不重要（并发执行），但保持稳定以便日志比对。

**任一失败不阻断其余**（`return_exceptions=True`）—— 质检算子挂了不该让预标注结果丢掉。执行器本身已把失败转成 `AlgoJobResult`，`gather` 兜住的是执行器自己的意外。

### manifest 构造是真实的

即便本地用子进程执行，manifest 仍真实产出并可断言：

| 设置 | 值 | 理由 |
|---|---|---|
| `ttlSecondsAfterFinished` | `RDH_ALGO_JOB_TTL_SECONDS`（300） | Job 完成后自动清理，否则集群堆满已完成 Job |
| `backoffLimit` | `0` | 重试由 Scheduler 控制，不交给 K8s —— 我们要记录每次失败 |
| `activeDeadlineSeconds` | `RDH_ALGO_JOB_TIMEOUT_SECONDS`（300） | 防止算子卡死占着 GPU |
| resources requests == limits | GPU 按 `GPU_REQUIREMENTS` | GPU 不可超卖 |
| `nodeSelector` | `accelerator: nvidia`（需 GPU 时） | 落到 GPU 节点 |

### 环境变量注入契约

见架构文档「Algo 算子 / 运行时契约」。镜像引用 `<registry>/algo-<operator>:<model_version>` —— tag 即模型版本。

### 本地替身

`RDH_ALGO_RUNNER=subprocess` 时用 `asyncio.create_subprocess_exec` 跑 `algo/` 下对应入口，记录真实起止时间，读算子产出的 `result.json` 生成 `AlgoJobResult`。切真集群时改的是 `KubernetesRunner.run()` 的提交方式。

---

## ⑧ Scheduler → Platform：结果回调

**凭据** `X-Scheduler-Token`。四个端点，**语义各不相同，不要合并**。

| 端点 | 驱动的迁移 | 触发时机 |
|---|---|---|
| `POST /callbacks/algo-result` | `processing` → `verification_pending` / `failed` | 解析流水线结束 |
| `POST /callbacks/annotation-processing` | `annotation_processing` → `annotation_pending` / `failed` | 送标环节结束 |
| `POST /callbacks/dataset-build` | —（只更新 dataset 记录） | 训练集构建结束 |
| `POST /callbacks/trigger-upload` | —（下发 WS 帧） | 管理员触发上传 |

### algo-result 的载荷与处理顺序

```
episode_id         Episode ID
results            本批算子结果（至少 1 条）
pipeline_complete  是否整条流水线完成
reported_at        回调时间（UTC）
```

处理顺序有讲究：

1. **每个算子的运行记录先落日志表**（成功/失败都记，供界面回溯这一阶段跑了什么）
2. 把产物合入 Episode（`segments` / `key_frames` / `quality`）
3. 按 `pipeline_complete` 决定是否推进状态

**单个算子完成只落数据，整条流水线完成才动状态。** `pipeline_complete=False` 时返回 `changed=False`。

失败时 `all_succeeded=False`，Platform 推到 `failed` 并把各算子的 `error_message` 拼进 `reject_reason`。

### annotation-processing 为何独立

源状态不同（`annotation_processing` 对 `processing`）。合成一个端点就得靠额外字段区分「我在哪个阶段」，回调方容易传错。

**失败也要回调** —— 算子挂了而不上报，Episode 会静默留在 `annotation_processing`，点过质检的人以为提交成功了。这与 `uploaded → processing` 踩过的坑同源。

本阶段送标不跑算子，所以没有产物要落库，但仍落一条 `algo_job_runs` 记录，让界面看到「解析」和「送标」是分开的两步。

### 409 被 Scheduler 视为成功

说明 Platform 侧状态已推进（重放），不该再重试。

---

## 错误码约定

Platform 统一错误响应（`platform/app/api/errors.py`）：

| HTTP | code | 含义 |
|---|---|---|
| 401 | `UNAUTHORIZED` | 认证失败。不回显具体原因（用户名是否存在等） |
| 404 | `EPISODE_NOT_FOUND` | Episode 不存在 |
| 404 | `RESOURCE_NOT_FOUND` | 其他资源不存在（兜底 `KeyError`） |
| 409 | `INVALID_STATE_TRANSITION` | 这条状态机边不存在 —— 流程不允许 |
| 409 | `UNEXPECTED_EPISODE_STATUS` | 当前不是这一步 —— 操作已失效，该刷新 |
| 422 | `CHECKSUM_MISMATCH` | 上传文件 checksum 与声明不符 |
| 422 | `VALIDATION_ERROR` | 请求体不合契约，`field` 指出第一个出错字段 |
| 500 | `EVENT_NOT_REGISTERED` | 发了未在契约注册的事件 —— 这是服务端 bug |
| 500 | `INTERNAL_ERROR` | 兜底，细节只进日志 |

两个 409 分开是有意的：前端据 code 就能决定提示「操作已失效，请刷新」还是「流程不允许」。

对外只回 `code` + 面向用户的 `message`，异常细节记服务端日志并用 `trace_id` 关联 —— 不把堆栈、SQL、内部路径回给调用方。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [`architecture.md`](architecture.md) | 架构总览、模块划分、状态机 |
| [`deployment.md`](deployment.md) | 本地运行与生产部署 |
| `../openapi/platform.yaml` | REST 契约（权威） |
| `../src/rdh_contract/ws/protocol.py` | WS 帧定义（权威） |
| `../src/rdh_contract/events/registry.py` | 事件注册表（权威） |
