# RobotDataHub — 部署指南

**契约版本** 0.1.0 · **最后核对** 2026-08-21（对照 HEAD 代码逐项核实）

分三部分：本地跑起来、把界面点起来、生产部署。架构见 [`architecture.md`](architecture.md)。

---

## 一、前置条件

| 工具 | 版本 | 用途 |
|---|---|---|
| Python | ≥ 3.12 | 全部 Python 模块 |
| [uv](https://docs.astral.sh/uv/) | 最新 | Python 依赖与虚拟环境管理 |
| Node.js | ≥ 18 | 两个前端 |
| pnpm | ≥ 9 | 前端依赖 |
| Docker | 最新 | 仅 `make broker-up` 需要 |

**本地不需要** PostgreSQL / MinIO / K8s —— 都有同接口的替身。RabbitMQ 是唯一可选的真服务。

---

## 二、最快验证：一条命令跑完全链路

```bash
make demo
```

约 1 秒完成，零外部依赖。8 条交互真跑一遍，输出 Episode 从 `recording` 到 `published` 的完整状态轨迹、算子产物统计、队列深度。

**这是验证环境是否可用的首选方式** —— 它不起任何服务，跑通说明依赖装对了、契约与实现是一致的。

### 走真 broker

```bash
cp .env.example .env    # 凭据不入库，仓库是 public
make broker-up          # 起 RabbitMQ（含管理台 :15672）
make demo-rabbit        # 同一份剧本，换成真 broker
make broker-down        # 停掉并清数据卷
```

`make demo` 与 `make demo-rabbit` 跑的是**同一份剧本**，只有 `RDH_QUEUE_BACKEND` 不同 —— 剧本能同时跑通两个后端，本身就是「切后端不用改调用方」的证据。

### 全量检查

```bash
make check
```

lint + 类型检查 + 契约覆盖率 + 前端 tsc + 前端单测 + 依赖铁律 + 契约一致性 + e2e。提 PR 前应该是绿的。

其余命令：

```bash
make contract-test      # 契约测试
make contract-cov       # 契约覆盖率（守 80%）
make contract-gen       # 生成 events/*.json + types/contract.ts
make conformance        # 跨模块契约一致性 + 依赖铁律（快，无需起服务）
make e2e                # 端到端流程测试
make rabbit-paths       # 真 broker 上验三条失败路径（需先 broker-up）
make clean-runtime      # 清掉本地运行数据（DB / 队列 / 对象存储）
make help               # 全部命令
```

---

## 三、把界面点起来

`make demo` 只走代码路径。要在浏览器里操作，需要起五个进程。

### 启动顺序

**Platform 必须先起** —— 其余三个都依赖它。

```bash
# 1. Platform API（其余的依赖它）
cd platform && uv run uvicorn app.main:app --reload --port 8000

# 2. Scheduler worker（常驻消费）
cd scheduler && uv run python -m scheduler.worker

# 3. Platform Web 控制台
cd platform/web && pnpm install && pnpm dev

# 4. Tool 工作台
cd tool && pnpm install && pnpm dev

# 5. Agent（常驻：WS 心跳 + 目录监听）
cd agent && uv run python -m agent.main --daemon
```

### 访问地址

| 服务 | 地址 | 说明 |
|---|---|---|
| Platform API 文档 | http://localhost:8000/docs | FastAPI 自动生成 |
| Platform Web | http://localhost:5173 | 端口被占时 vite 自动挪到 5174 等 |
| Tool 工作台 | http://localhost:5178 | |
| RabbitMQ 管理台 | http://localhost:15672 | 仅 `broker-up` 后可用 |

两个前端都把 `/api` 代理到 `127.0.0.1:8000`，所以不存在跨域问题。

### 准备演示数据

空库里没有任务和 Episode，界面上看不到东西。先跑一次：

```bash
make demo
```

它会建好用户、任务、Agent 节点，并跑出一条走到 `published` 的 Episode。

### 演示账号

全部密码 `demo-only-pass`：

| 账号 | 角色 | 用途 |
|---|---|---|
| `admin` | admin | Platform Web 全部工作区 |
| `recorder` | recorder | 采集工作区 |
| `annotator` | annotator | Tool 三个工作台（当前都用它） |
| `verifier` | verifier | 契约里有此角色，Tool 暂未分开使用 |
| `reviewer` | reviewer | 同上 |

Tool 用 `annotator` **自动登录**，正常不需要手动输入。若看到登录页，说明该账号不存在或密码不对 —— 跑一次 `make demo` 重建种子数据。

### 常见启动问题

**`Address already in use`（8000）** —— 上一次的 uvicorn 还在。`lsof -iTCP:8000 -sTCP:LISTEN -P -n` 找出 PID 后 kill。

**vite 提示端口被占用** —— 它会自动换端口并在日志里打出实际地址，按日志的地址访问即可。注意 Platform Web 若挪了端口，Tool 深链跳转仍按 `VITE_TOOL_BASE_URL`（默认 `http://localhost:5178`）走。

**Agent 报 `Using SOCKS proxy, but the 'socksio' package is not installed`** —— 环境里有 `all_proxy=socks5://...`，httpx 在构造 client 时就会为 SOCKS 建 transport，不看 `NO_PROXY`，所以即便目标是 127.0.0.1 也躲不过。启动时摘掉代理变量：

```bash
env -u all_proxy -u ALL_PROXY -u http_proxy -u HTTP_PROXY \
    -u https_proxy -u HTTPS_PROXY \
    uv run python -m agent.main --daemon
```

**界面上 Episode 状态不推进** —— 检查 Scheduler worker 是否在跑。Platform 只发事件，推进状态靠 Scheduler 回调。

**Platform 与 Scheduler 一边发一边收不到** —— 两者的 `RDH_QUEUE_BACKEND` 必须一致。

### Agent 的其他运行模式

```bash
uv run python -m agent.main --task-id <task_id>   # 采集一条就退出
uv run python -m agent.main --recover             # 只跑断电恢复
uv run python -m agent.main --daemon             # 常驻：WS + 目录监听
```

### 单独跑一个算子

Scheduler 就是这么调的：

```bash
cd algo
RDH_JOB_ID=j1 RDH_EPISODE_ID=e1 RDH_OPERATOR=quality \
RDH_INPUT_PATH=/path/to/raw.mcap RDH_OUTPUT_DIR=/tmp/out \
  uv run python -m operators.quality.main
```

---

## 四、配置项

全部环境变量前缀 `RDH_`，各模块从 `.env` 或环境读取。下表只列需要关注的，完整清单见各模块 `config.py`。

### Platform

| 变量 | 默认 | 说明 |
|---|---|---|
| `RDH_ENVIRONMENT` | `local` | `local` / `staging` / `production` |
| `RDH_API_PREFIX` | `/api/v1` | API 路径前缀 |
| `RDH_DATABASE_URL` | SQLite 本地文件 | 生产填 PostgreSQL DSN |
| `RDH_QUEUE_BACKEND` | `file` | `file` / `rabbit`，**须与 Scheduler 一致** |
| `RDH_AMQP_URL` | 本地 guest | `rabbit` 后端必填 |
| `RDH_OBJECT_STORE_ROOT` | `.runtime/objects` | 生产改 MinIO |
| `RDH_JWT_SECRET` | demo 值 | **生产必须覆盖** |
| `RDH_JWT_TTL_SECONDS` | `3600` | JWT 有效期 |
| `RDH_AGENT_TOKEN` | demo 值 | **生产必须覆盖**，交互③凭据 |
| `RDH_SCHEDULER_TOKEN` | demo 值 | **生产必须覆盖**，交互⑧凭据 |
| `RDH_HEARTBEAT_TIMEOUT_SECONDS` | `45` | 心跳超时判离线 |

### Scheduler

| 变量 | 默认 | 说明 |
|---|---|---|
| `RDH_QUEUE_BACKEND` | `file` | 须与 Platform 一致 |
| `RDH_PLATFORM_BASE_URL` | `http://127.0.0.1:8000/api/v1` | 回调地址 |
| `RDH_SCHEDULER_TOKEN` | demo 值 | 须与 Platform 的一致 |
| `RDH_ALGO_RUNNER` | `subprocess` | `subprocess` / `kubernetes` |
| `RDH_ALGO_IMAGE_REGISTRY` | `robotdatahub` | 算子镜像仓库前缀 |
| `RDH_ALGO_MODEL_VERSION` | `v0.1.0` | 模型版本 = 镜像 tag |
| `RDH_ALGO_JOB_TIMEOUT_SECONDS` | `300` | 算子超时 |
| `RDH_ALGO_JOB_TTL_SECONDS` | `300` | Job 完成后清理延迟 |
| `RDH_MAX_RETRIES` | `3` | 兜底重试；单事件以契约声明为准 |

### Agent

| 变量 | 默认 | 说明 |
|---|---|---|
| `RDH_AGENT_ID` | `agent-local-01` | Agent 唯一标识，多台须区分 |
| `RDH_PLATFORM_BASE_URL` | `http://127.0.0.1:8000/api/v1` | |
| `RDH_PLATFORM_WS_URL` | `ws://127.0.0.1:8000/api/v1/ws/agent` | |
| `RDH_AGENT_TOKEN` | demo 值 | 须与 Platform 的一致 |
| `RDH_WATCH_ROOT` | `.runtime/agent/tasks` | 目录监听根 |
| `RDH_CHUNK_SIZE_BYTES` | 256 KiB | 分片大小 |
| `RDH_MAX_UPLOAD_RETRIES` | `3` | 单分片重试上限 |
| `RDH_RECONNECT_MAX_SECONDS` | `30` | 重连退避上限 |

### 三个必须成对一致的值

```
RDH_QUEUE_BACKEND     Platform ←→ Scheduler
RDH_AGENT_TOKEN       Platform ←→ Agent
RDH_SCHEDULER_TOKEN   Platform ←→ Scheduler
```

不一致的表现是静默失败或 401，不好定位。

---

## 五、生产部署

> 以下是**设计意图**，本阶段未实际部署过。逐项标注了当前状态。

### 5.1 启动守卫会拦住 demo 凭据

`RDH_ENVIRONMENT=production` 时，`assert_production_ready()` 检查这几项是否仍是默认值：

```
RDH_JWT_SECRET
RDH_AGENT_TOKEN
RDH_SCHEDULER_TOKEN
RDH_AMQP_URL        （仅 rabbit 后端）
```

任一未覆盖就抛错拒绝启动 —— 宁可起不来，也不带着 demo 密钥上线。

### 5.2 需要替换的替身

| 替身 | 生产 | 替换方式 | 状态 |
|---|---|---|---|
| SQLite | PostgreSQL | `RDH_DATABASE_URL` 换 DSN | 同一套 SQLAlchemy 模型，可直接切 |
| 本地目录 | MinIO | `dependencies.get_object_store()` 换实现 | **需实现 MinIO ObjectStore** |
| 文件队列 | RabbitMQ | `RDH_QUEUE_BACKEND=rabbit` | 已实现，可直接切 |
| 子进程 | K8s Job | `RDH_ALGO_RUNNER=kubernetes` | **`KubernetesRunner.run()` 未实现** |

后两项是生产化的主要工作量。`k8s/job_builder.py` 的 manifest 构造已经是真实的且有测试，缺的只是提交给集群这一步。

### 5.3 数据库迁移

**当前 demo 用 `create_all`，`alembic/versions/` 是空的。** 生产前必须补首个迁移：

```bash
cd platform
uv run alembic revision --autogenerate -m "initial schema"
uv run alembic upgrade head
```

8 张表见架构文档。两张只追加的日志表（`episode_transitions` / `algo_job_runs`）**没有归档或清理策略** —— 只增不删，长期运行需要补。

### 5.4 各模块的部署形态

| 模块 | 形态 | 副本 | 备注 |
|---|---|---|---|
| Platform API | K8s Deployment | 多副本 | 无状态，可水平扩 |
| Platform Web | 静态资源 | CDN / Nginx | `pnpm build` 产出 |
| Tool | 静态资源 | CDN / Nginx | 同上 |
| Scheduler worker | K8s Deployment + KEDA | 0~150 | 按队列深度伸缩 |
| Algo 算子 | K8s Job（动态创建） | 按需 | GPU 节点，TTL 自动清理 |
| Agent | 裸机 / Docker | 每台采集 PC 一个 | 在客户网络内，不接受入站连接 |

### 5.5 弹性伸缩

`scheduler/deploy/keda-scaledobject.yaml` 已就位。KEDA 按 RabbitMQ 队列深度伸缩，**队列名取自契约的 `JobType`** —— 这是为什么契约的队列名必须是权威来源。

注意两套队列并存且不能混：领域事件队列（`ingest` / `algo` / `tool` / `notify`）与 Celery 任务队列（前缀 `celery.`）。混进同一队列，消费方会把对方的消息当垃圾丢掉。

### 5.6 前端构建

```bash
cd platform/web && pnpm build    # tsc -b && vite build
cd tool && pnpm build
```

产物是静态资源。生产需配置 `/api` 反代到 Platform，并放行 WebSocket 的 `Upgrade` 头 —— `/api/v1/ws/console` 与 `/api/v1/ws/agent` 都走同一前缀。

Tool 的基址由 `VITE_TOOL_BASE_URL` 决定（Platform Web 的深链用它），构建时注入。

### 5.7 算子镜像

```bash
cd algo
docker build -f operators/quality/Dockerfile -t <registry>/algo-quality:v0.1.0 .
```

四个算子各一个 Dockerfile。**镜像 tag 即模型版本**，Scheduler 按 `RDH_ALGO_MODEL_VERSION` 选版本。

### 5.8 安全清单

- [ ] 四个 `RDH_*` 凭据全部显式设置（启动守卫会检查）
- [ ] `RDH_ENVIRONMENT=production`（否则守卫不生效）
- [ ] RabbitMQ 不对公网暴露（本地 compose 已只绑 127.0.0.1）
- [ ] Platform API 前置 TLS
- [ ] 服务令牌与 JWT 密钥进 secret manager，不进镜像或环境文件
- [ ] Agent 令牌按采集点分发，便于单点吊销（当前是全局一个值）

### 5.9 尚未设计的部分

监控与告警、日志聚合、备份与恢复、灰度发布 —— 本阶段均未涉及。

---

## 六、故障排查

### 看日志

各模块用标准 `logging`，格式 `时间 级别 模块: 消息`。建议起服务时重定向到文件：

```bash
uv run uvicorn app.main:app --reload > /tmp/rdh-platform.log 2>&1
```

### 查本地运行数据

```bash
sqlite3 .runtime/platform.db "SELECT episode_id, status FROM episodes;"
sqlite3 .runtime/platform.db "SELECT from_status, to_status, occurred_at FROM episode_transitions ORDER BY id;"
sqlite3 .runtime/platform.db "SELECT operator, status, model_version FROM algo_job_runs ORDER BY started_at;"
ls .runtime/queue/          # 文件队列各队列目录
ls .runtime/dlq/            # 死信
ls .runtime/processed/      # 已处理归档（仅 file 后端）
ls .runtime/objects/        # 对象存储
```

### Episode 卡住了怎么查

1. 查它的**当前状态**和**流转轨迹** —— 轨迹能看出卡在哪一步、停了多久
2. 若停在 `processing` 或 `annotation_processing`（两个自动环节），检查 Scheduler worker 是否在跑
3. 查 `algo_job_runs` 看算子是否失败
4. 查 `dlq/` 看事件是否进了死信
5. 若停在 `verification_pending` / `annotation_pending` / `annotation_review`，那是**在等人**，不是故障

### 状态推进报 409

两个不同含义，看 `code`：

| code | 含义 | 处理 |
|---|---|---|
| `UNEXPECTED_EPISODE_STATUS` | 当前不是这一步（多为重复提交） | 刷新页面重新取队列 |
| `INVALID_STATE_TRANSITION` | 这条状态机边不存在 | 检查调用逻辑，流程不允许这么走 |

### 清干净重来

```bash
make clean-runtime    # 清 DB / 队列 / 对象存储，保留依赖
make demo             # 重建种子数据并跑一遍
```

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [`architecture.md`](architecture.md) | 架构总览、模块划分、状态机 |
| [`interactions.md`](interactions.md) | 8 条交互的载荷与失败处理 |
| 各模块 `README.md` | 单模块的运行命令与设计要点 |
| `../../Makefile` | 全部命令（`make help`） |
| `../../.env.example` | 环境变量模板 |
