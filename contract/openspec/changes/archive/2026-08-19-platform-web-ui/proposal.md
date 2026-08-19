# Platform Web UI 实现

> **实现完成后已更新本文档**：初版 proposal 把「WebSocket 实时推送」「任务详情页」
> 列入「明确不做」，实现过程中用户要求补上，且实现中发现并修复了 4 个跨模块缺陷。
> 下文描述的是**最终交付范围**，与初版的偏离在「与初版 proposal 的偏离」一节列出。

## 动机

Platform 当前只有 REST API 和 WebSocket 端点，无可视化界面。运维人员需通过 curl 或
Postman 观察 Agent 状态、任务进度和 Episode 流转，效率低、易出错。本 change 实现
Platform 自有前端，覆盖任务下发 → 采集回传 → 流水线推进的完整可观测链路。

## 目标

**新增模块**：`platform/web/` — React 18 + Vite 6 + Ant Design 5 单页应用

**交付范围**：

1. **登录页** — 持久化 JWT，启用后端 RBAC 鉴权
2. **任务管理（admin）** — 任务列表、新建任务、选 Agent 下发；点任务进入详情
3. **任务详情（admin）** — 一个任务下的全部子任务（Episode），按阶段汇总
4. **采集记录（admin/recorder）** — 跨任务的历史视图，可按任务与子状态筛选
5. **运维监控（admin/recorder）** — Agent 节点状态查看 + 触发回传
6. **实时推送** — 浏览器 WS 订阅 Agent 上下线与上传进度，轮询降级兜底
7. **阶段视图** — 把契约的 10 个 Episode 状态在展示层收成 5 个大阶段
8. **工作区按角色收敛** — 与后端 `require_roles` 对齐，admin 通配

**明确不做**（留给后续 change）：

- 核验 / 标注 / 审核工作区（依赖 Tool 模块的多视角播放器与时间轴编辑器）
- 训练集构建界面
- 前端路由（当前用 state 切换二级页，刷新回列表、后退键不生效）
- Episode 级数据权限隔离（当前只校验 role，Recorder A 能看到 B 的数据）
- 组件级单测与 E2E 自动化（原型项目简化原则）

## 影响的模块

- **Contract** — **有改动**：新增两个浏览器下行帧（`console.agent_status`、
  `console.upload_progress`）、`Episode.recorded_by` 字段；重新生成 `types/contract.ts`
- **Platform** — 新增 `/ws/console` 端点、`GET /users`、`GET /episodes?task_id=`；
  修复 `uploaded → processing` 状态机断链；时间戳统一带时区
- **Agent** — 修复目录监听的路径过滤缺陷与 `create_episode` 状态码断言
- **Scheduler** — 无代码改动，但其算子流水线因 Platform 的状态机修复才真正可用
- **Tool** — 无直接依赖（独立前端，共用 contract types 不共用组件）

## 实现中发现并修复的缺陷

这些不在初版 proposal 里 —— 都是接前端时被真实链路暴露出来的。

### 1. `uploaded → processing` 无人执行（跨模块状态机断链）

Scheduler 的注释写着「Platform 侧的 `uploaded → processing` 由它自己在收到本回调前
完成」，而 Platform 的 `start_processing()` 方法存在但**没有任何路由或事件处理器调用它**。
`grep` 的唯一命中是 `scripts/demo.py` 与 `testing/e2e/test_full_pipeline.py` —— 两者
各自手动补了这一跳，所以它们能跑通，真实链路静默卡死。

链路实际表现：Episode 停在 `uploaded` → Scheduler 跑完 4 个算子 → 回调 `algo-result`
想推进到 `verification_pending` → 状态机只允许 `uploaded → processing`，非法 → 409。
而 409 被 Scheduler 当成「重放，视为成功」咽掉并 ack 事件，日志一切正常，状态一步没动、
算子产物也没落库。

修复：Platform 在发出 `episode.uploaded` 后自己推进到 `processing`（方案对比见
`design.md` 的「上传完成后谁负责推进到 processing」）。同时删掉 demo 与 e2e 里的手动
补跳 —— 它们本质是在替生产代码打补丁，掩盖了缺陷。

### 2. 目录监听误杀所有文件（Agent）

`watcher.py` 的过滤条件检查**绝对路径**的每一段是否以点开头，本意是排除 `.uploading/`
等阶段子目录。但默认运行目录是 `.runtime/` —— 以点开头，于是监听根目录下任何文件都命中
这条 return，watchdog 的实时事件全部失效。`scan_existing()` 走另一条路（只看文件名），
所以「触发回传」能入队，掩盖了问题。

修复：只检查相对 `watch_root` 的路径段。原有测试用 `tmp_path`，路径里永远没有点号段，
这是缺陷溜过去的原因；已补两个用例把监听根目录放在 `.runtime/` 下。

### 3. `create_episode` 把 201 当失败（Agent）

Platform 的 `POST /episodes` 声明 `status_code=201`，Agent 客户端断言 `!= 200` 即报错，
于是每个外部落地的文件登记 Episode 时都失败、被移进 `.failed/`。修复为接受 200/201，
并新增 `tests/test_platform_client.py` 把各端点的成功码钉住。

### 4. 时间戳丢时区，前端差 8 小时

SQLite 没有原生 datetime，`DateTime(timezone=True)` 对它是空操作：写入的 tz-aware 值
读出来变成 naive，序列化成 `2026-08-19T03:06:24`（无偏移）。浏览器按本地时间解析，
北京时区下整整差 8 小时。库里存的时刻一直正确，丢的只是标记。

修复：新增 `UtcDateTime` 列类型在数据库边界收口（写入归一到 UTC、读出补回 tzinfo），
前端显式按 `Asia/Shanghai` 格式化而非跟随浏览器时区。

## 附带修复

- **pnpm 装不上依赖** — 两个 `pnpm-workspace.yaml` 的 `allowBuilds.esbuild` 值是字面
  占位串 `set this to true or false`，pnpm 11 读到它直接让 install 失败，导致
  `make web-check` 与 `make tool-check` 长期无法执行。
- **任务表单默认 topics 与录制器不符** — 默认填 `/camera/front, /arm/joint_states`，
  而录制器产出的是 `/camera/front/image_raw, /joint_states, /gripper/state`，按默认值
  建的任务，文件一落地就被预检拒收。

## 新增工具

`scripts/mock_record.py` — 模拟采集软件往任务目录写 MCAP，驱动 Agent 的监听链路。
任务名作位置参数（精确匹配优先于子串，否则短名字是长名字前缀时永远选不中）；
`--list` 会标出 topic 与录制器不符、跑之前就知道会被拒收的任务。

## 验证方式

```bash
make check   # 契约 170 / platform 28 / agent 96 / conformance 43 / e2e 7 + 依赖铁律 + 两个前端类型检查
make demo    # 8 条核心交互跑通到 published
```

**手动验证链路**（四个进程）：

```bash
# Platform / 前端 / Agent 常驻 / Scheduler
platform/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
cd platform/web && pnpm exec vite
cd agent && .venv/bin/python -m agent.main --daemon
cd scheduler && .venv/bin/python -m scheduler.worker

# 建任务并下发后，模拟采集
agent/.venv/bin/python scripts/mock_record.py <任务名> --done-marker
```

实测结果：Episode 自动走到 `verification_pending`，算子产物全部落库（3 个分段、
质检报告、10 个关键帧），`algo-result` 回调返回 200。

## 已知阻塞

Episode 会稳定停在 `verification_pending` —— 核验与标注是人工环节，而对应界面不存在
（依赖 Tool 模块）。因此「完成」阶段恒为 0，`published_count` 在当前界面里无法增长。
要让链路走到底，需二选一：

1. 补核验 / 标注界面（工作量大，依赖 Tool 的播放器与时间轴组件）
2. 把 Episode 状态机截短，以质检通过为终态（改契约，影响 Platform / Tool / Testing）

**此决定尚未做出**，留待后续 change。

## 后续工作

- 核验 / 标注 / 审核工作区
- 前端路由（react-router），支持刷新保持位置与浏览器后退
- Episode 级数据权限隔离
- Celery 替换文件队列（下一阶段工作，本 change 的状态机修复已为其铺路）
