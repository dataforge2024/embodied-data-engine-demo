# robotdatahub-platform

核心业务后端 + WebSocket 服务 + 事件发布。FastAPI + SQLAlchemy（本地 SQLite / 生产 PostgreSQL）。

## 我依赖 contract 的什么

| 契约项 | 用途 |
|---|---|
| `state_machine` | `services/episode_lifecycle.py` 用 `assert_transition()` 守卫每次状态变更 |
| `enums` | 状态、角色、算子类型；DB 存字符串值，不用数据库枚举 |
| `schemas` | API 请求/响应模型直接用契约模型，不重写一遍 |
| `events` | `services/event_publisher.py` 按 `EVENT_REGISTRY` 校验并投递 |
| `ws/protocol` | `ws/handlers.py` 用 `UPSTREAM_ADAPTER` 解析上行帧 |

## 我暴露什么

- **REST API** —— 契约见 `contract/openapi/platform.yaml`，前缀 `/api/v1`
- **WebSocket** —— `/api/v1/ws/agent`，首帧必须 `up.register`
- **RabbitMQ 事件** —— `episode.uploaded` / `episode.rejected` / `annotation.approved`

## 我参与哪几条交互

| # | 角色 | 实现位置 |
|---|---|---|
| ① | WS 服务端 | `app/ws/manager.py`、`app/ws/handlers.py` |
| ③ | 接收 Agent 上传回调 | `app/api/routes/callbacks.py::upload_complete` |
| ④ | 向 Tool 提供 REST | `app/api/routes/review.py` |
| ⑤ | 发布事件 | `app/services/event_publisher.py` |
| ⑧ | 接收 Scheduler 结果回调 | `app/api/routes/callbacks.py::algo_result` |

③ 和 ⑧ 是两个不同端点，凭据也不同（`X-Agent-Token` vs `X-Scheduler-Token`）。

## 两个必须收口的关注点

1. **状态变更** —— 只能经 `services/episode_lifecycle.py`。`repositories/episode.py` 的
   `apply_transition()` 不做合法性校验，绕过 lifecycle 直接调它就绕过了状态机。
2. **事件发布** —— 只能经 `services/event_publisher.py`。它校验 routing_key 已注册且 payload
   类型匹配，错发的事件在这里被拦住而不是让 Scheduler 反序列化失败。

## 本地替身

| 生产 | 本地 | 替换点 |
|---|---|---|
| PostgreSQL | SQLite (aiosqlite) | `RDH_DATABASE_URL` |
| RabbitMQ | 文件队列 / **真 broker** | `RDH_QUEUE_BACKEND=file\|rabbit` |
| MinIO | 本地目录 | `dependencies.get_object_store()` |

三处都是协议 + 实现分离，换实现不动调用方。

队列后端有两个实现，都满足 `EventPublisher` 协议：

| 后端 | 实现 | 投递目标由谁决定 |
|---|---|---|
| `file` | `event_publisher.py::FileQueuePublisher` | 发布方按 `consumer_queue` 建目录 |
| `rabbit` | `rabbit_publisher.py::RabbitPublisher` | binding —— **Platform 只声明 exchange** |

信封格式两者共用 `event_publisher.py::prepare_event()`，Scheduler 按同一结构解析两个后端。

发布方不声明队列是有意的：有哪些消费队列是 Scheduler 的事，发布方不该知道。

## 运行

```bash
uv sync
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs
RDH_QUEUE_BACKEND=rabbit uv run uvicorn app.main:app    # 走真 broker
```

真 broker 需要先在仓库根起：`make broker-up`（凭据见 `.env.example`）。

生产环境必须显式设置 `RDH_JWT_SECRET` / `RDH_AGENT_TOKEN` / `RDH_SCHEDULER_TOKEN`，
用 `rabbit` 后端时还要设 `RDH_AMQP_URL` —— 否则启动时 `assert_production_ready()` 会拒绝启动。

## 已知不足（POC 阶段接受）

**发布与落库不在一个事务里。** `mark_uploaded()` 先 `publish()`，再由路由层 `session.commit()`。
RabbitMQ 下消息即时且持久，若随后 commit 失败，Scheduler 会消费到一条 Platform 里并不存在
对应状态的事件。反向也成立：broker 不可达时 publish 抛异常 → commit 不发生 → 上传回调整体
失败 → Agent 重试，这是安全的（无孤儿），但意味着 **broker 故障会阻塞上传链路**。

生产解法是 transactional outbox。POC 不做，理由见
`openspec/changes/scheduler-celery-rabbitmq/design.md`。
