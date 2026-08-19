# Scheduler 接 RabbitMQ 与 Celery

## Why

Scheduler 的骨架已经能跑，但消息层是**文件队列替身**，三条关键语义靠进程内内存维持，
换真 broker 时会立刻暴露：

- 幂等去重的 `self._seen` 是内存 `set`，进程重启即失忆
- 重试计数 `self._attempts` 同样在内存
- 重试无延迟。AMQP 里 nack + requeue 会让失败消息立刻回队首，与消费者形成热循环

后两件 Celery 原生解决（`request.retries` 随消息持久化、`retry_backoff` 延迟重投），
自己手写最容易出错。

顺带两处**契约与实现的错位**，接 broker 后会更刺眼：

- `algo.completed` / `algo.failed` 契约里声明、notify worker 订阅，但**零处生产代码发布** ——
  notify worker 是个订阅了两个永不到达的事件的空转进程
- `dataset.build_requested` 被发布、被订阅，但 `handle()` 里没有分支，落到兜底 `else` 被
  **静默 ack** —— 消息确认了，活儿没干

## What Changes

四层分工：

```
Platform     aio-pika 发领域事件，不知道谁消费、怎么消费
   ↓
RabbitMQ     契约定义的 topic exchange + 4 队列   ← KEDA 直接读这层队列深度
   ↓
薄消费层     按契约校验 payload，合格才进 Celery
   ↓
Celery       退避重试、重试计数、并发池
             task 体内跑算子、回调 Platform      ← 以后接 K8s 也落在这里
```

**AMQP 拓扑归契约，伸缩归 KEDA，任务可靠性归 Celery，算子执行归 Scheduler。**

为什么 Platform 不直接调 Celery：它一旦 `send_task()` 就得装 celery 并知道 Scheduler 的
task 名，破了「模块间不得直接 import」的铁律。详细依据见 design。

具体改动：

- **契约**：删 `algo.completed` / `algo.failed` 及对应 payload 模型，重新生成 TS 类型与
  events JSON。其余不动 —— exchange / routing_key / consumer_queue / max_retries 继续作为
  AMQP 拓扑的单一事实来源
- **Platform**：新增 `RabbitPublisher` 实现现有 `EventPublisher` Protocol
- **Scheduler**：新增 `RabbitConsumer`（与 `FileQueueConsumer` 同样的 `fetch`/`ack`/`reject`）、
  Celery app 与四个 task、薄消费层
- **补分支**：`dataset.build_requested` 记录 + 标示未实现；兜底 `else` 改警告级别
- **配置切换**：`RDH_QUEUE_BACKEND=file|rabbit`，两侧共用

## 四项取舍

| 取舍 | 决定 | 依据 |
|---|---|---|
| 文件队列留还是弃 | **留**，配置切换 | `make demo` 是唯一能一条命令验证全链路的东西，让它依赖 Docker 会让日常验证变重 |
| `algo.completed` / `algo.failed` | **删** | HTTP 回调已能工作；保留只是让 worker 有活干 |
| `dataset.build_requested` | **补分支 + 占位** | 先消除静默丢弃这个 bug，导出格式是独立工作量 |
| K8s | **缓** | 本机 `kubectl cluster-info` 连不上；算子镜像是 `nvidia/cuda` 基础镜像，ARM 无 GPU 跑不了。`job_builder.py` 的 manifest 已是真实的，缺的只是 `run()` 里的提交动作 |

## 影响的模块

- **Contract** — 删两个事件与 payload 模型。破坏性改动，但两个事件零处引用，实际影响面为零
- **Platform** — 加 aio-pika 依赖。**不依赖 celery**（`make arch-check` 会守住）
- **Scheduler** — 加 celery + aio-pika
- **Testing** — conformance 若断言事件数量需同步；e2e 继续走文件队列
- **Agent / Algo / Tool** — 不受影响

## 明确不做

- K8s 真接与 KEDA 部署（`deploy/keda-scaledobject.yaml` 继续留档）
- 训练集导出格式（LeRobot / RLDS），单开 change
- 去重表 —— 见 design 的幂等决策

## 验证方式

```bash
make check          # 含依赖铁律：Platform 不得依赖 celery
make demo           # 文件队列，零外部依赖
make demo-rabbit    # 同一条链路走真 broker
```

三条失败路径必须在 **RabbitMQ 后端**上验证 —— 只在文件队列上跑证明不了 broker 路径正确：
payload 不合契约进死信、重试耗尽进死信、同一消息投两次结果不变。

## 风险

1. **幂等依赖「重跑无害」这个假设。** 当前实现恰好满足（算子输出覆盖同名 object_key、
   `algo-result` 撞状态机守卫返回 409 被当重放咽掉），但这是**当前**实现的性质而非被强制的
   约束。缓解：写成 spec 硬要求

2. **发布与落库不在一个事务里。** `mark_uploaded()` 先 `publish()` 再由路由层
   `session.commit()`。文件队列下这只是留个孤儿文件；RabbitMQ 下消息是即时且持久的，
   若随后 commit 失败，Scheduler 会消费到一条 Platform 里并不存在对应状态的事件。
   反向也成立：broker 不可达时异常会让 commit 不发生，**上传回调整体失败**，Agent 重试 ——
   这是安全的（无孤儿），但意味着 broker 故障会阻塞上传链路。
   POC 阶段**接受**这两点，不做 transactional outbox；但要在实现时确认失败行为符合上述预期，
   而不是留下静默不一致

3. **两条消息路径的 bug 可能不重叠。** 文件队列跑通不代表 RabbitMQ 也对。缓解：上面那组
   失败路径测试在 RabbitMQ 上跑
