# Scheduler 接 RabbitMQ 与 Celery 任务清单

POC 规模。目标是**证明这套分层能跑通**，不是做生产级消息基础设施。

## 1. 契约：删两个死事件

- [x] 1.1 从 `EVENT_REGISTRY` / `EVENT_MODELS` 删 `algo.completed` / `algo.failed`
- [x] 1.2 删 `AlgoCompleted` / `AlgoFailed` 模型与导出
- [x] 1.3 重新生成 `types/contract.ts` 与 `events/*.json`，更新受影响的契约测试

## 2. 发布方（Platform）

- [x] 2.1 新增 `RabbitPublisher`，实现现有 `EventPublisher` Protocol
- [x] 2.2 只声明 exchange，不建队列 —— 投递目标由 binding 决定
- [x] 2.3 `RDH_QUEUE_BACKEND=file|rabbit` 开关；`FileQueuePublisher` 保留

## 3. 消费方（Scheduler）

- [x] 3.1 新增 `RabbitConsumer`，与 `FileQueueConsumer` 同样的 `fetch`/`ack`/`reject`
- [x] 3.2 队列与绑定启动时声明，绑定 key 取自 `routing_keys_for_queue()`
- [x] 3.3 Celery app + 四个 task，`max_retries` 取自契约的事件声明
- [x] 3.4 薄消费层：契约校验通过才 `task.delay()`，不合契约直接进死信
- [x] 3.5 补 `dataset.build_requested` 分支（记录 + 标示未实现），兜底 `else` 改警告级别

## 4. 本地编排

- [x] 4.1 `docker-compose.yml` 加 rabbitmq（含 management 端口便于看队列深度）
- [x] 4.2 凭据经环境变量注入 —— **仓库是 public，值不得入库**
- [x] 4.3 `make demo-rabbit` 走真 broker；`make demo` 保持零外部依赖

## 5. 验证

- [x] 5.1 `make check` 全绿（含依赖铁律：Platform 不得依赖 celery）
- [x] 5.2 `make demo` 与 `make demo-rabbit` 都跑通 8 条交互
- [x] 5.3 三条失败路径在 **RabbitMQ 后端**上验证：payload 不合契约进死信、
      重试耗尽进死信、同一消息投两次结果不变
- [x] 5.4 更新 `scheduler/README.md` 与 `platform/README.md` 的替身表格

## 不属于本 change

- [ ] K8s 真接 —— 落点是 `KubernetesRunner.run()`，由 Celery task 调用。前置条件：
      本机有可用集群 + 算子镜像能在开发机跑（现在是 `nvidia/cuda` 基础镜像，
      ARM 无 GPU 跑不了）
- [ ] KEDA 部署 —— 依赖真集群，`deploy/keda-scaledobject.yaml` 继续留档
- [ ] 训练集导出格式 —— 单开 change
