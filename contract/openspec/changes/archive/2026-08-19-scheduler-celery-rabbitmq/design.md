# Scheduler 接 RabbitMQ 与 Celery 设计

## 1. Celery 放在哪一层

讨论起点是一个说法：「RabbitMQ 是 Celery 里面的东西，对外部透明」。这话**对一半** ——
Celery 确实封装了 AMQP，写 `@app.task` 和 `task.delay()` 的人不碰 channel、exchange、ack。
但透明的前提是**生产方和消费方都在 Celery 里**，这套架构不满足：Platform 是发布方。

| 方案 | Platform 怎么发 | 契约的事件注册表 | 代价 |
|---|---|---|---|
| 甲 Celery 全包 | `celery.send_task()` | 废弃，改 task 名 | 破铁律、KEDA 失去权威队列名、文件队列路径报废 |
| **乙 Celery 只管执行** | aio-pika 发领域事件 | **不变** | 多一层薄消费层 |
| 丙 只接 RabbitMQ | aio-pika 发领域事件 | 不变 | 自己实现退避重试与计数持久化 |

**选乙。**

### 为什么不是甲

**① Platform 不能是 Celery 客户端。** 它的 `pyproject.toml` 只有 contract / fastapi /
sqlalchemy。用 `send_task()` 就得装 celery 并知道 Scheduler 的 task 名 —— 而 task 名是
Scheduler 的内部命名，`make arch-check` 拦的就是这种耦合。

**② 契约已把 AMQP 拓扑写成共享事实。** `EXCHANGE_MAIN`、`EXCHANGE_DLX`、`routing_key`、
`consumer_queue`、按事件声明的 `max_retries` 在 `contract/events/registry.py`，六个模块都依赖。
换成 Celery task 名后队列名失去权威来源：「Celery 的队列命名」「KEDA 的 `queueName`」
「四类 worker 的 Deployment 名」三处得手工保持一致，而它们不在同一个仓库、没有编译期检查。

**③ KEDA 绕过 Celery 直接读 broker。**

```yaml
triggers:
  - type: rabbitmq
    metadata: { protocol: amqp, queueName: algo, mode: QueueLength, value: "5" }
```

四个 `ScaledObject` 全是这个形状，查队列深度伸缩 Pod（algo 队列 0~150 副本）。队列名
`ingest`/`algo`/`tool`/`notify` 来自契约的 `JobType` 枚举 —— 是部署层的载荷，不是实现细节。

**④ 消息格式不兼容（最硬）。** Celery protocol v2 把 task 名放 AMQP header、body 是
`[args, kwargs, embed]`；Platform 发的是领域事件信封（`routing_key`/`event_id`/`payload`）。
Celery worker 收到领域事件认不出是任务，当 unregistered 丢掉。而**文件队列里存的就是领域
事件** —— 已定「文件队列保留」，这一条直接排除甲。

### 为什么不是丙

Celery 解决两件自己手写容易错的事：

| 问题 | 文件队列现状 | Celery |
|---|---|---|
| 重试计数 | 进程内 dict，重启即失忆 | `request.retries` 随消息持久化 |
| 重试延迟 | 立刻重试 | `retry_backoff` 原生延迟重投 |

丙方案下重试延迟得自己搭 DLX + message TTL，topology 从「1 exchange + 4 queue」变成
「2 exchange + 4 queue + 4 retry queue + 1 dlq」。这是 Celery 已经做好的事。

### 薄消费层做什么

按 routing_key 查 `EVENT_REGISTRY` 拿模型 → `model_validate` 校验 → 失败进死信、
成功 `task.delay()`。四个队列共用一份代码，配置驱动。

**校验放在进 Celery 之前是有意的**：不合契约的 payload 不该占用重试预算 —— 重试一条格式
错误的消息永远不会成功。这与 `FileQueueConsumer.fetch()` 现有行为一致。

## 2. 幂等：靠消费幂等，不建去重表

RabbitMQ 是至少一次投递。Celery 的 `request.retries` 解决重试计数，不解决重复投递。
文件队列现在用进程内 `self._seen` 去重，重启即失忆 —— 换 broker 后彻底失效。

**选「让消费本身幂等」**，两个理由。

一是当前实现**已经幂等**：

| 步骤 | 重跑一次的后果 |
|---|---|
| 算子执行 | 输出覆盖同名 `object_key`，结果一致 |
| `algo-result` 回调 | 撞状态机守卫返回 409，被 Scheduler 当重放咽掉 |
| 任务计数 | `increment_counters` 只在状态真变化时调，409 路径不重复累加 |

代价是重投浪费一次算力。接 K8s 后是一次 GPU Job —— 可接受，重投是异常路径而非常态。

二是**去重表本身是一致性陷阱**：先记 event_id 再处理，中途崩溃则消息永久丢失；先处理再记，
崩溃时仍会重跑 —— 等于没去重。要做对得把「记录」和「处理」放进同一事务，而这跨了
Scheduler 与对象存储两个系统。

代价：幂等从基础设施保证变成**每个 handler 的责任**，必须写成 spec 硬要求，否则新增 handler
时无人知晓。

## 3. 文件队列保留，靠配置切换

`RDH_QUEUE_BACKEND=file|rabbit`。两侧抽象都已就位，**调用方不用改**：

| 侧 | 抽象 | 现有 | 新增 |
|---|---|---|---|
| 发布 | `EventPublisher` Protocol | `FileQueuePublisher` / `NullPublisher` | `RabbitPublisher` |
| 消费 | `fetch` / `ack` / `reject` | `FileQueueConsumer` | `RabbitConsumer` |

## 4. 删掉 algo.completed / algo.failed

这两个 routing_key 契约里声明、notify worker 订阅，但 `grep` 只命中契约自身的定义与导出 ——
**零处生产代码发布**。算子结果走 HTTP 回调 `algo-result` 已经能工作，改成事件驱动会多一跳，
好处只是让 worker 有活干。删。将来算子失败真需要告警重试时再引入 —— 那时它有真实职责。

## AMQP 拓扑映射

| 契约 | AMQP |
|---|---|
| `EXCHANGE_MAIN` | topic exchange，durable |
| `EXCHANGE_DLX` | 死信 exchange |
| `EventSpec.routing_key` | binding key |
| `EventSpec.consumer_queue`（`JobType`） | 队列名 |
| `EventSpec.max_retries` | Celery task 的 `max_retries` |
| `routing_keys_for_queue(queue)` | 该队列的 binding key 列表 |

队列与绑定由 **Scheduler** 启动时声明，Platform 只声明 exchange —— 发布方不该知道有哪些
消费队列。这与文件队列现状有差异：`FileQueuePublisher` 按 `consumer_queue` 建目录，等价于
发布方在决定投递目标；RabbitMQ 下这由 binding 决定。

## 接受的语义差异

| 语义 | 文件队列 | RabbitMQ | 处置 |
|---|---|---|---|
| 消费顺序 | 文件名时间戳前缀 | 多副本并发后无序 | **接受**：顺序从来不是正确性依赖，乱序事件会撞状态机守卫而非产生错误状态 |
| 归档留证 | `processed/` 目录 | ack 后消息即消失 | 接受：排查靠日志与 Platform 的状态轨迹 |
| 死信 | `dlq/` 目录 | `EXCHANGE_DLX` | 一一对应 |

## 已知不足（POC 阶段接受）

**发布与落库不在一个事务里。** `mark_uploaded()` 先 `publish()`，再由路由层 `session.commit()`。
文件队列下失败只留个孤儿文件；RabbitMQ 下消息即时且持久，若随后 commit 失败，Scheduler 会
消费到一条 Platform 里并不存在对应状态的事件。

反向也成立：broker 不可达时 publish 抛异常 → commit 不发生 → 上传回调整体失败 → Agent 重试。
这是安全的（无孤儿），但意味着 **broker 故障会阻塞上传链路**。

生产解法是 transactional outbox（先把事件写进同一个 DB 事务，再由单独的投递器发出）。
POC 不做，但实现时要确认失败行为符合上述预期，而不是留下静默不一致。

## 后续

**K8s 真接**落点明确：`KubernetesRunner.run()` 提交 Job 并轮询，调用它的是 Celery task。
前置条件是本机有可用集群 + 算子镜像能在开发机跑（现在是 `nvidia/cuda` 基础镜像，
ARM 无 GPU 跑不了，可能需要 CPU 版或多架构构建）。
