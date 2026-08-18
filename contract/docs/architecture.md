# RobotDataHub — 架构设计文档

**日期**: 2026-08-14  
**版本**: v1.0  
**核心**: 模块划分 + 交互架构 + Claude Code 开发

---

## 一、整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                      RobotDataHub 架构                        │
└──────────────────────────────────────────────────────────────┘

    采集PC端                          云端平台
┌─────────────┐                  ┌──────────────────────────┐
│   Agent     │─────WebSocket────▶│      Platform           │
│  (采集工具)  │                  │     (核心业务)           │
│             │                  │  FastAPI + React + PG    │
│  Python     │                  └─────────┬────────────────┘
│  WebSocket  │                            │
│  MinIO SDK  │                            │ RabbitMQ
│             │                            ▼
│             │                  ┌──────────────────────────┐
│             │                  │      Scheduler           │
│             │                  │     (调度底座)           │
│             │                  │  Celery + RabbitMQ       │
│             │◀───上传完成回调───│                          │
└──────┬──────┘                  │  • ingest-worker         │
       │                         │  • tool-worker           │
       │ 分片上传                 │  • algo-worker           │
       ▼                         │  • notify-worker         │
┌─────────────┐                  └─────────┬────────────────┘
│   MinIO     │                            │
│ (对象存储)   │                            │ K8s API
└─────────────┘                            ▼
                                 ┌──────────────────────────┐
┌─────────────┐                  │     Algo 算子             │
│    Tool     │◀────HTTP API─────│   (K8s Job 动态创建)      │
│ (质检/标注)  │                  │   PyTorch + GPU          │
│   React     │                  └──────────────────────────┘
└─────────────┘
                                 ┌──────────────────────────┐
                                 │      Testing             │
                                 │  pytest + Playwright     │
                                 └──────────────────────────┘
```

**核心交互**:
1. **Agent → Platform**: WebSocket 长连接（心跳、任务推送、状态同步）
2. **Agent → MinIO**: 分片上传 MCAP 文件
3. **Agent → Platform**: 上传完成后 HTTP 回调
4. **Tool ↔ Platform**: HTTP REST API（核验、标注）
5. **Platform → RabbitMQ**: 发布事件（episode.uploaded, annotation.approved）
6. **Scheduler**: 消费 RabbitMQ 事件，触发 Celery 流水线
7. **Scheduler → K8s**: 创建 Job 运行 Algo 算子
8. **Scheduler → Platform**: HTTP 回调处理结果

---

## 二、模块功能划分

### 模块1: 工具（Tool）— 质检与标注

**技术栈**: React 18 + TypeScript

**核心功能**:
- 多视角视频回放（支持多路同步）
- 时间轴编辑器（缩放、拖拽、分段）
- 动作分段与描述
- 核验工作流（通过/打回）
- 标注审核工作流（通过/退回）

---

### 模块2: 平台（Platform）— 核心业务

**技术栈**: 
- 后端: FastAPI + PostgreSQL
- 前端: React 18 + Ant Design 5

**核心功能**:
- 任务管理（Admin 工作区）
- 采集管理（Recorder 工作区）
- WebSocket 服务（Agent 心跳、任务推送）
- 核验队列管理
- 标注队列管理
- 训练集构建（Lab 工作区）
- 运维监控（SysOps 工作区）
- JWT 认证 + RBAC 权限
- RabbitMQ 消息发布

**Episode 状态流转**:
```
recording → uploading → uploaded → processing 
  → verification_pending → annotation_pending 
  → annotation_review → published
```

---

### 模块3: 调度底座（Scheduler）— 数据处理流水线

**技术栈**: Celery + RabbitMQ + K8s + KEDA

**核心功能**:
- RabbitMQ 事件消费
- 数据处理流水线编排
- 弹性伸缩（KEDA 0~150副本）

**4类 Worker**:
1. **ingest-worker**: MCAP 解析、关键帧抽取、数据索引
2. **tool-worker**: 格式转换、训练集构建
3. **algo-worker**: 创建 K8s Job 运行算法推理
4. **notify-worker**: 消息通知、回调 Platform

---

### 模块4: 算法开发（Algo）— GPU 推理算子

**运行方式**: K8s Job/Pod（由 Scheduler 动态创建）

**核心功能**:
- 预标注算子（动作分段识别）
- 质检算子（模糊/遮挡检测）
- 关键帧识别算子
- 异常检测算子

**技术栈**: PyTorch / TensorFlow + Docker + K8s

**关键特性**:
- Job 完成后自动清理（TTL）
- 模型版本管理（镜像 tag）
- GPU 资源隔离

---

### 模块5: 采集工具（Agent）— 采集PC端

**技术栈**: Python 3.12 + WebSocket + MinIO SDK

**核心功能**:
- WebSocket 长连接（心跳、任务推送）
- MCAP 录制
- 分片上传（断点续传）
- 本地持久化（SQLite）
- 上传完成 HTTP 回调
- 断电恢复

---

### 模块6: 测试（Testing）— 质量保障

**技术栈**: pytest + Playwright + Locust

**核心功能**:
- 单元测试
- 集成测试（API + 数据库）
- E2E 测试（关键流程）
- 性能测试（压测）
- 测试覆盖率 ≥ 80%

---

## 三、Scheduler 调用 Algo 流程

### 流程说明

1. **RabbitMQ 消费事件**: Scheduler 监听 `episode.uploaded` 事件
2. **Celery 创建 K8s Job**: algo-worker 调用 K8s API 创建 Job，传递参数（episode_id, mcap_path, model_version）
3. **K8s 调度 Pod**: K8s Scheduler 在 GPU 节点上启动 Pod，拉取算法镜像
4. **Pod 内运行算法算子**: 从 MinIO 读取 MCAP → GPU 推理 → 结果写入 MinIO
5. **获取结果并清理**: Celery 任务监听 Job 状态，成功后获取结果，Job 自动清理（TTL 5分钟）

### 架构层次

```
应用层: Celery Task (algo-worker)
    ↓ Kubernetes API
编排层: K8s Job
    ↓ K8s Scheduler
执行层: K8s Pod (动态创建，GPU 节点)
    • 拉取算法镜像
    • 从 MinIO 读取 MCAP
    • GPU 推理
    • 结果写入 MinIO
    • Pod 完成后自动清理
```

---

## 四、开发规范

### 代码组织

1. **代码库独立**: 各模块独立代码库，各自选择开发工具和流程
2. **contract 库**: Mono-repo 架构，存放全局文档、接口定义、数据模型
   - API 契约（OpenAPI 规范）
   - 数据模型（共享 Schema）
   - 消息定义（RabbitMQ 事件格式）
   - 部署配置模板

### OpenSpec 开发流程

1. **Propose**: 在 contract 库提出变更 Proposal（需求、设计、影响分析）
2. **Review**: 团队 Review，确认接口变更、数据模型、依赖关系
3. **Apply**: 各模块根据 Proposal 独立实现
4. **Archive**: 实现完成后归档 Proposal

### TDD 开发要求

1. **测试先行**: 编写功能前先写测试用例
2. **测试覆盖率**: ≥ 80%，关键路径 100%
3. **测试类型**:
   - 单元测试（纯函数、业务逻辑）
   - 集成测试（API、数据库、消息队列）
   - E2E 测试（关键用户流程）

### AI 辅助开发

1. **使用 Claude Code**: 代码生成、审查、测试、重构
2. **人工主导**: 架构设计、技术决策、安全审查由人完成
3. **代码 Review**: AI 生成代码必须人工 Review

### 代码质量

- 通过 Lint + 格式化（black/ruff/prettier）
- 代码风格统一
- 错误处理完善
- 安全检查通过（SQL注入/XSS/CSRF）

---

## 五、技术栈总结

| 模块 | 后端 | 前端 | 消息 | 存储 | 部署 |
|---|---|---|---|---|---|
| **Platform** | FastAPI | React 18 | RabbitMQ (生产) | PostgreSQL | K8s |
| **Scheduler** | Celery | - | RabbitMQ (消费) | Redis | K8s+KEDA |
| **Agent** | Python 3.12 | - | WebSocket | SQLite | 裸机/Docker |
| **Tool** | 可选 | React 18 | - | - | K8s |
| **Algo** | PyTorch/TF | - | - | - | K8s Job |
| **Testing** | pytest | Playwright | - | - | CI/CD |

---

## 六、模块依赖关系

```
Tool ─────HTTP API─────▶ Platform
Agent ◀───WebSocket────▶ Platform
Agent ────分片上传──────▶ MinIO
Agent ────上传回调─────▶ Platform
Platform ──RabbitMQ────▶ Scheduler
Scheduler ─K8s API─────▶ Algo (K8s Job)
Scheduler ─HTTP回调────▶ Platform
Testing ───横向覆盖────▶ All Modules
```

**核心依赖说明**:
- Tool 和 Agent 都依赖 Platform 提供的 API
- Platform 通过 RabbitMQ 与 Scheduler 解耦
- Scheduler 动态创建 K8s Job 运行 Algo 算子
- Testing 横向覆盖所有模块的质量保障

---

**文档完成！**
