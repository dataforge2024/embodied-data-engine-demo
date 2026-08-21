# robotdatahub-scheduler

事件消费 + 流水线编排 + K8s Job 创建。生产为 Celery + RabbitMQ + KEDA。

## 我依赖 contract 的什么

| 契约项 | 用途 |
|---|---|
| `events.EVENT_REGISTRY` | `consumers/queue.py` 按 routing_key 反序列化；`routing_keys_for_queue()` 决定各 worker 订阅什么 |
| `events` payload 模型 | 消费时校验消息合契约，不合的进死信 |
| `schemas.scheduler` | `AlgoJobSpec` 传给算子、`AlgoResultCallback` 回调 Platform |
| `enums.JobType` | 4 类 worker 队列划分 |
| `enums.AlgoOperator` | 算子类型与镜像名映射 |

**不依赖** Platform 的任何代码 —— 只通过 HTTP 回调交互。

## 我暴露什么

不暴露 API。对外行为是：

- 消费 RabbitMQ 事件
- 创建 K8s Job 运行 Algo 算子
- HTTP 回调 Platform 写回结果

## 我参与哪几条交互

| # | 角色 | 实现位置 |
|---|---|---|
| ⑥ | 消费事件触发流水线 | `consumers/queue.py`、`worker.py` |
| ⑦ | 创建 K8s Job 运行算子 | `k8s/job_builder.py`、`k8s/runner.py` |
| ⑧ | 回调 Platform | `callbacks/platform.py` |

## 4 类 worker

按契约的 `consumer_queue` 自动分派，代码里不硬编码 routing_key：

| worker | 订阅 | 职责 |
|---|---|---|
| ingest | `episode.uploaded` | 解析 MCAP、抽关键帧、串算子流水线 |
| algo | （由 ingest 内部调度） | 创建 K8s Job 跑算子 |
| notify | `episode.rejected` | 回调 Platform、告警 |
| tool | `annotation.approved` / `dataset.build_requested` | 格式转换、训练集构建 |

`RDH_QUEUE_BACKEND=file` 时是**一个进程内 4 个消费循环**（`worker.py`）；
`=rabbit` 时是 4 个薄消费层把领域事件翻译成 Celery 任务（`rabbit_worker.py`），
生产再叠上 KEDA 按队列深度伸缩 0~150 副本（`deploy/keda-scaledobject.yaml`）。
三种形态的消费逻辑完全相同 —— 都靠 `routing_keys_for_queue()` 分派。

## 本地替身

| 生产 | 本地 | 替换点 |
|---|---|---|
| RabbitMQ | 文件队列 / **真 broker** | `RDH_QUEUE_BACKEND=file\|rabbit` |
| K8s Job | 子进程 | `RDH_ALGO_RUNNER=subprocess`（`k8s/runner.py`） |
| MinIO | 本地目录 | `RDH_OBJECT_STORE_ROOT` |

队列后端有两个实现，`fetch` / `ack` / `reject` 三个动作同形：

| 后端 | 实现 | 死信 | 归档 |
|---|---|---|---|
| `file` | `consumers/queue.py::FileQueueConsumer` | `dlq/` 目录 | `processed/` 目录 |
| `rabbit` | `consumers/rabbit.py::RabbitConsumer` | `EXCHANGE_DLX` | 无（ack 后即消失） |

信封解码两者共用 `consumers/event.py::decode_envelope` —— 格式漂移会让切后端时静默失败。

`k8s/job_builder.py` 的 manifest 构造是**真实的**（TTL、GPU limits、backoffLimit 都按生产设置），
只是本地不提交给集群。切真集群时改的是 `KubernetesRunner.run()` 的提交方式。

## Celery 在哪一层

**只管执行，不管收发领域事件。** Platform 用 aio-pika 发领域事件，薄消费层校验通过后
`task.delay()`。理由见 `openspec/changes/archive/2026-08-19-scheduler-celery-rabbitmq/design.md` 第 1 节，
简版是三条：Platform 不能是 Celery 客户端（破依赖铁律）、契约的队列名是 KEDA 的权威来源、
Celery protocol v2 的消息体与领域事件信封不兼容。

因此**两套队列并存且必须分开**：领域事件队列名取自 `JobType`（`ingest` / `algo` / `tool` /
`notify`），Celery 任务队列前缀 `celery.` —— 混进同一个队列，消费方会把对方的消息当垃圾丢掉。

每个 task 的 `max_retries` 取自契约的事件声明，不在代码里硬编码。

## 保留的消费语义

文件队列不是玩具替身，以下性质与 RabbitMQ 一致：

- **幂等去重** —— 按 `event_id` 记录已处理集合，重放跳过
- **重试与死信** —— 重试到契约声明的 `max_retries`，耗尽进 `dlq/`
- **消费顺序** —— 文件名带时间戳前缀
- **原子投递** —— 发布方用临时文件 + rename，消费方读不到半个文件

切到 RabbitMQ 后有两处**接受的差异**：消费顺序在多副本下无序（顺序从来不是正确性依赖，
乱序事件会撞状态机守卫而非产生错误状态），归档留证消失（排查靠日志与 Platform 的状态轨迹）。

幂等在 RabbitMQ 下**是每个 handler 的责任**，不是基础设施保证 —— 没有去重表。
当前实现已经幂等：算子输出覆盖同名 `object_key`，回调撞状态机守卫返回 409 被当重放咽掉。
新增 handler 时必须自己保证这一点。

## 运行

```bash
uv sync
uv run python -m scheduler.worker           # 文件队列后端，常驻消费
RDH_QUEUE_BACKEND=rabbit uv run python -m scheduler.rabbit_worker   # 真 broker
uv run celery -A scheduler.celery_app worker -Q celery.ingest,celery.tool,celery.notify
```

真 broker 需要先在仓库根起：`make broker-up`（凭据见 `.env.example`）。
