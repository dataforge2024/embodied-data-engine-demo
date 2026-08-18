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
| notify | `algo.completed` / `algo.failed` / `episode.rejected` | 回调 Platform、告警 |
| tool | `annotation.approved` / `dataset.build_requested` | 格式转换、训练集构建 |

本地是**一个进程内 4 个消费循环**；生产是 4 组独立 Celery worker + KEDA 按队列深度伸缩
0~150 副本（`deploy/keda-scaledobject.yaml`）。消费逻辑完全相同。

## 本地替身

| 生产 | 本地 | 替换点 |
|---|---|---|
| RabbitMQ | 文件队列 | `consumers/queue.py::FileQueueConsumer` |
| K8s Job | 子进程 | `RDH_ALGO_RUNNER=subprocess`（`k8s/runner.py`） |
| MinIO | 本地目录 | `RDH_OBJECT_STORE_ROOT` |

`k8s/job_builder.py` 的 manifest 构造是**真实的**（TTL、GPU limits、backoffLimit 都按生产设置），
只是本地不提交给集群。切真集群时改的是 `KubernetesRunner.run()` 的提交方式。

## 保留的消费语义

文件队列不是玩具替身，以下性质与 RabbitMQ 一致：

- **幂等去重** —— 按 `event_id` 记录已处理集合，重放跳过
- **重试与死信** —— 重试到契约声明的 `max_retries`，耗尽进 `dlq/`
- **消费顺序** —— 文件名带时间戳前缀
- **原子投递** —— 发布方用临时文件 + rename，消费方读不到半个文件

## 运行

```bash
uv sync
uv run python -m scheduler.worker      # 常驻消费
```
