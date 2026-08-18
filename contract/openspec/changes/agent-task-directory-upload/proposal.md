## Why

当前 Agent 的主循环是「等 Platform 推任务 → 自己模拟录制 → 上传到本地目录」。这个形态无法接入真实采集流程：采集软件产出的 MCAP 文件在采集 PC 的磁盘上，没有任何机制把它们送进流水线。同时上传目标是本地文件系统替身，数据从未真正离开开发机。

结果是交互①②③（Agent ↔ Platform 的任务下发、上传、回调）虽然协议层完整，却没有一条真实数据流经过它们。这条链路是整个平台的入口，它不通，下游的核验、标注、训练集构建都只能跑在构造数据上，无法向技术团队与产品演示。

本变更把 Agent 从「模拟录制器」改造为「目录守护进程」：管理员下发任务时 Agent 自动创建对应目录，采集人员把 MCAP 文件放入目录即触发上传至阿里云 OSS，全程经 WebSocket 实时回传进度。

## What Changes

**任务下发（交互①）**

- Agent 收到 `TaskPushFrame` 后在监听根目录下创建任务目录，目录名为 `<slug(任务名)>__<task_id>`
- 目录内写入 `.task.json`，包含任务快照与 `TaskRequirement`，供采集人员离线查阅采集要求
- 新增 `GET /agents/me/tasks` 端点，Agent 启动或 WS 重连后拉取已分派任务并重建目录
  - 补齐 `platform/app/api/routes/tasks.py` 中已在注释里承诺但从未实现的行为（"推送失败不回滚分派 —— Agent 重连后会拉取已分派任务"）
- Agent 收到 `TaskCancelFrame` 后：已开始上传的文件传完，未开始的移入 `.cancelled/`，目录改名标记

**目录监听与上传（交互②）**

- Agent 主循环由「等任务推送」改为 `watchdog` 监听文件系统事件 — **BREAKING**（Agent 启动语义变更，`--task-id` 模拟采集模式保留但不再是主路径）
- 文件写入完成检测：文件大小连续 3 次采样不变，或出现同名 `.done` 标记文件
- MCAP 格式嗅探：按文件头区分标准 MCAP（magic `\x89MCAP0\r\n`）与本项目 JSON Lines 格式，分别解析出 topic 列表与时长
- 本地预检：解析出的 topic 不满足 `TaskRequirement.required_topics` 时拒绝上传，文件移入 `.rejected/` 并写入原因，避免无效的大文件传输
- 新增 `OSSObjectStore`，实现既有 `ObjectStore` protocol，替换 `LocalObjectStore`
- 文件生命周期：待检测 → 待上传 → `.uploading/` → `.done/`，失败路径进 `.failed/` 并附 `.error` 说明
- 每完成一个分片发送 `UploadProgressFrame`

**上传进度回传**

- `Episode` 新增 `upload_progress` 字段
- Platform 侧进度落库改为节流写入（进度变化 ≥5% 或距上次写入 ≥2 秒），替换当前的 `logger.debug` 丢弃
  - 当前"不落库"的设计使前端刷新后进度归零；节流在保留实时性的同时把 500MB 文件的 2000 次 UPDATE 降到约 20 次

**上传完成回调（交互③）**

- 回调携带真实 checksum 与从 MCAP 实际解析出的 `recorded_topics`，替换当前的构造值

**心跳**

- `AgentHeartbeat` 的 `pending_upload_count` 与 `disk_free_bytes` 改为真实值（当前为模拟值）

**打包与部署**

- Agent 新增 Dockerfile 与 docker-compose 配置，`/watch` 作为 volume 挂载点
- 新增 `.env.oss.example`；OSS 凭据经 gitignored 的 `.env.oss` 注入，不入库

**前置修复**

- 统一运行时目录：`scripts/demo.py` 写入 `.runtime/demo` 而 `platform/app/core/config.py` 默认 `.runtime`，导致 demo 产生的数据对手工启动的服务不可见
- 补充用户种子数据：当前无任何用户记录，登录必然失败，Agent 亦无法登录

## Capabilities

### New Capabilities

- `agent-task-directory`: 任务目录的创建、命名、元数据文件、生命周期标记，以及 Agent 重启后依据已分派任务重建目录
- `agent-file-watch`: 文件系统监听、写入完成检测、MCAP 格式嗅探与解析、采集要求本地预检、文件在待处理/上传中/已完成/失败/拒绝各阶段间的流转
- `agent-oss-upload`: 阿里云 OSS 对象存储实现、预签名凭据获取、分片上传与断点续传、上传完成回调
- `agent-progress-reporting`: 上传进度经 WebSocket 回传、Platform 侧节流落库、心跳携带真实队列长度与磁盘水位

### Modified Capabilities

无。`openspec/specs/` 目前为空，本变更是首个 change，所有能力均为新增。

契约层的两处增量（`GET /agents/me/tasks` 端点、`Episode.upload_progress` 字段）属于新增而非既有需求变更，收敛在上述新能力的 spec 中。

## Impact

**契约（`contract/`）— 0.1.0 → 0.2.0**

| 项 | 变更 |
|---|---|
| `openapi/platform.yaml` | 新增 `GET /agents/me/tasks` |
| `schemas/episode.py` | `Episode` 新增 `upload_progress` 字段 |
| `types/contract.ts`、`events/*.json` | 生成物需重跑 `make contract-gen` |

`ws/protocol.py` 无需改动 — `AgentTaskPush` 已含 `task_id` / `task_name` / `requirement`，`UploadProgressFrame` 已具备回传进度所需字段。

**Agent（`agent/`）**

| 文件 | 变更 |
|---|---|
| `main.py` | 主循环重写为目录守护模式 |
| `config.py` | 新增监听根目录、稳定性采样、OSS 连接配置 |
| `collector.py` | 由"录制 + 上传"缩小为"上传"，录制职责移出 |
| 新增 `watcher/` | 目录监听、稳定性检测、文件流转 |
| 新增 `mcap/reader.py` | 格式嗅探与 topic/时长解析 |
| 新增 `oss/store.py` | `ObjectStore` 的 OSS 实现 |
| 新增 `Dockerfile` | 容器打包 |
| `pyproject.toml` | 新增 `watchdog`、`oss2`、`mcap` 依赖 |

**Platform（`platform/`）**

| 文件 | 变更 |
|---|---|
| `api/routes/agents.py` | 新增 `GET /agents/me/tasks` |
| `ws/handlers.py` | `UploadProgressFrame` 分支改为节流落库 |
| `models/episode.py`、`repositories/episode.py` | 新增进度字段与节流更新方法 |
| `core/config.py` | 运行时目录默认值与 `scripts/demo.py` 对齐 |
| 新增用户种子 | Alembic 迁移或启动时幂等 seed |

**Testing（`testing/`）**

- `contract_checks/`：新增端点须在一致性校验中成对出现
- `e2e/`：新增目录监听→上传→回调链路用例；既有 7 个用例因 Agent 启动语义变更需复核

**外部依赖**

- 阿里云 OSS：需 bucket、endpoint 区域、RAM 子账号 AK/SK（仅授权目标 bucket）
- OSS 须配置「清理未完成分片」生命周期规则 —— 失败的分片上传会持续计存储费用，此项只能在控制台配置

**不在本变更范围**

前端进度条与任务界面、RabbitMQ 替换 `FileQueuePublisher`、核验与标注界面、K8s Job 接入。Scheduler 继续使用 `SubprocessRunner` 执行算子，交互⑦维持模拟。
