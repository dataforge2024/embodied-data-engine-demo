# Platform Web UI 设计

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    浏览器（vite dev server）                      │
│  LoginPage → localStorage token → App（认证守卫 + 角色收敛）      │
│    ↓                                                             │
│  工作区（顶栏 tab，按 can(...roles) 过滤）                        │
│    ├─ admin           TasksPage ⇄ TaskDetailPage（二级页）        │
│    ├─ admin/recorder  EpisodesPage（跨任务历史 + 筛选）           │
│    └─ admin/recorder  SysOpsPage（纯查看 + 触发回传）             │
│                                                                  │
│  共享层                                                          │
│    components/EpisodeTable  两个页面共用一份列定义                │
│    components/StageBar      10 状态 → 5 阶段的展示分组            │
│    hooks/useConsoleStream   WS 帧折叠成页面可用状态               │
│    api/client.ts            fetch wrapper + JWT 注入              │
└──────────────────────────────────────────────────────────────────┘
        ↓ HTTP（Vite proxy）              ↓ WS（同一前缀，ws: true）
┌──────────────────────────────────────────────────────────────────┐
│                  Platform API (127.0.0.1:8000)                   │
│  /auth/login              → JWT                                  │
│  /users                   → user_id → display_name 反查           │
│  /tasks, /tasks/{id}      → 列表与详情                            │
│  /tasks/{id}/assign       → 分派并 WS 推给 Agent                  │
│  /episodes?task_id=&status= → 子任务列表（父子关系的数据基础）     │
│  /agents                  → 节点与在线状态                        │
│  /ws/console?token=<JWT>  → 单向推送：上下线 + 上传进度            │
└──────────────────────────────────────────────────────────────────┘
```

**父子关系**：任务是父，Episode（子任务）是子 —— 一次采集上传即建一条。
任务详情页是这层关系的主视角；采集记录页是跨任务的历史视图，两者共用 `EpisodeTable`。

## 核心决策

### 1. 认证流程：JWT + localStorage

**选择**：前端持久化 token，每请求带 `Authorization` header

**备选**：
- HttpOnly cookie — 更安全（防 XSS 窃取），但跨域配置复杂，原型阶段不值当
- Session — 需要后端 Redis 状态，不符合「本地替身跑全链路」原则

**依据**：
- 原型项目不处理生产级安全（`CLAUDE.md` 明确「代码会被丢弃」）
- localStorage + JWT 是最简实现，契约 `openapi/platform.yaml` 已定义 `bearerAuth`
- 过期处理简单：401 → 清 token → 跳登录页

```typescript
// api/client.ts
let accessToken: string | null = null;
export function setAccessToken(token: string | null): void {
  accessToken = token;
}
function headers(): Record<string, string> {
  const base = { "Content-Type": "application/json" };
  if (accessToken) base.Authorization = `Bearer ${accessToken}`;
  return base;
}

// App.tsx
useEffect(() => {
  const token = localStorage.getItem("rdh_access_token");
  if (token) {
    setAccessToken(token);
    setAuthenticated(true);
  }
}, []);
```

### 2. 状态同步：WS 推送 + 轮询兜底

**选择**：浏览器订阅 `/ws/console` 收即时通知，各页面同时保留 5s/10s 轮询

初版决策是「只轮询」，实现中用户要求补实时推送。最终不是二选一，而是**两者并存**：
WS 负责让状态变化立刻可见，轮询负责兜住 WS 覆盖不到的字段与断线期间的变化。

**契约新增两个下行帧**（`console.` 前缀区别于 Agent 的 `down.` 帧）：

```python
class ConsoleAgentStatusFrame(ContractModel):
    type: Literal[MessageType.CONSOLE_AGENT_STATUS]
    agent_id: str
    online: bool
    hostname: str | None
    at: datetime


class ConsoleUploadProgressFrame(ContractModel):
    type: Literal[MessageType.CONSOLE_UPLOAD_PROGRESS]
    episode_id: str
    agent_id: str
    uploaded_parts: int  # 与上行帧同单位
    total_parts: int
    percent: float  # Platform 算好，前端不各算一遍
```

进度帧的计量单位是**分片数而非字节数**。初版实现声明了 `uploaded_bytes`/`total_bytes`，
但 Agent 的上行 `UploadProgressFrame` 只上报分片进度 —— 声明字节就得凭空造数。
这个错位在接前端时暴露：`handlers.py` 用分片数调用，manager 的签名要字节数，
每个节流后的进度帧都会 `TypeError`。

**鉴权走 query 参数** `?token=<JWT>`：浏览器 `new WebSocket()` 带不了自定义 header，
这是标准做法。仅 admin 与 recorder 可连，拒绝码 4401（token 无效）/ 4403（角色无权）。

**单向推送**：浏览器发来的帧一律忽略，所有操作走 REST。连接池与 Agent 的分开维护，
发送失败的连接就地摘除 —— 浏览器关标签页时 FastAPI 不一定已触发 disconnect。

**降级不是错误**：WS 断开时页面靠轮询继续工作，所以 UI 用弱提示（「轮询」标签）
而不是报错。前端退避重连 1s → 15s，4401/4403 不重连（同一 token 重试无意义）。

### 3. 类型来源：contract 生成物，零手写副本

**选择**：`import type { Episode, CollectTask } from "@contract"`，`@contract` alias 指向 `contract/types/contract.ts`

**备选**：
- 手写前端类型 — 必然漂移，已发现 4 处不一致
- 用 OpenAPI Generator 从 `platform.yaml` 生成 — 只覆盖 REST，WebSocket 帧仍需手写；且生成物冗长（每个 model 一个文件）

**依据**：
- 契约层是唯一事实来源（`project.md` 铁律）
- `export_ts_types.py` 已覆盖所有 schema + enums，包括 WS 帧
- TypeScript 的 structural typing 让别名无成本：`@contract` 的 Episode 和 API 响应的 Episode 类型兼容

```typescript
// vite.config.ts
resolve: {
  alias: {
    "@contract": path.resolve(__dirname, "../../contract/types"),
  },
}

// tsconfig.json
{
  "compilerOptions": {
    "paths": {
      "@contract": ["../../contract/types"]
    }
  }
}
```

### 4. UI 组件库：Ant Design 5 暗色主题

**选择**：Ant Design 5 + `theme.darkAlgorithm` + 定制 token

**备选**：
- 无框架（纯 CSS + 原生组件）— 表单校验、modal、toast 都要手写，工作量大
- Tailwind + Headless UI — 更灵活但配置繁琐，原型不需要极致定制
- MUI — 比 Ant Design 重，中文文档少

**依据**：
- Ant Design 是 React 生态最成熟的企业级组件库，暗色主题开箱即用
- `ConfigProvider` + token 覆盖让定制色板（cyan 主色、深海军蓝底）只需 10 行
- Table / Form / Modal / Message 都是高频组件，Ant 质量稳定

```typescript
<ConfigProvider
  theme={{
    algorithm: theme.darkAlgorithm,
    token: {
      colorPrimary: "#38BDF8",       // cyan accent
      colorBgBase: "#0A0D12",        // 最深底色
      colorBgContainer: "#0F131C",   // 卡片背景
      colorBorder: "#161D2B",        // 边框
      borderRadius: 8,
    },
  }}
>
```

### 5. 任务下发：两步串行（创建 + 分派）

**选择**：表单提交时先 `POST /tasks` 创建草稿，再 `POST /tasks/{id}/assign` 分派给 Agent

**备选**：
- 一步到位接口 `POST /tasks/assign-to-agent` — 需后端新增端点，且分派失败时任务已创建但未关联 Agent，状态混乱
- 前端合并为一个表单，后端接口不变 — 当前选择

**依据**：
- 契约 `openapi/platform.yaml` 明确分成两个端点（创建 201、分派 200）
- 两步流程清晰：draft → assigned，中间状态可观测
- 分派失败时任务留在 draft，SysOps 可重试或删除

```typescript
const task = await createTask({ name, description, requirement });
await assignTask(task.task_id, targetAgent);  // 失败不回滚 task 创建
```

## 数据模型映射

| 页面 | 后端接口 | Contract 类型 | 前端展示 |
|---|---|---|---|
| LoginPage | `POST /auth/login` | `TokenResponse` | JWT → localStorage |
| TasksPage | `GET /tasks` | `PaginatedResponse<CollectTask>` | 表格：名称、状态、进度、负责 Agent |
| EpisodesPage | `GET /episodes` | `PaginatedResponse<Episode>` | 表格：ID、状态、时长、大小、分段数 |
| SysOpsPage | `GET /agents` | `ApiResponse<AgentNode[]>` | 表格：ID、主机名、在线状态、心跳详情 |
| SysOpsPage modal | `POST /tasks` + `POST /tasks/{id}/assign` | `CollectTask` + `TaskAssignment` | 表单 → 下发确认 |

### 关键字段对齐

**TasksPage 进度条**：
- `task.published_count` / `task.requirement.target_episode_count` → 百分比
- 从 `task.assignments[]` 最后一条取 `agent_id`（最新分派即当前负责方）

**EpisodesPage 状态判定**：
- `isTerminal(episode.status)` 判断是否终态（imported from `@contract`）
- 终态行 `opacity: 0.65`，不再变化

**SysOpsPage 在线判定**：
- `agent.online`（后端由心跳超时判定，3 个心跳周期 = 45s）
- `agent.last_heartbeat.pending_upload_count` 显示待上传队列长度

## 样式规范

### CSS 变量层级

```css
:root {
  /* 表面层（5 级深度） */
  --surface-0: #05070c;  /* 最深底色 */
  --surface-1: #0a0d12;  /* 顶栏 */
  --surface-2: #0f131c;  /* 主容器 */
  --surface-3: #161d2b;  /* 卡片 / 表头 */
  --surface-4: #1e2636;  /* hover 态 */
  
  /* 强调色 */
  --accent: #38bdf8;      /* cyan，主 CTA / 链接 */
  --accent-dim: #0e7490;  /* 进度条渐变起点 */
  
  /* 文字层级 */
  --text-primary: #e2e8f0;    /* 主文字 */
  --text-secondary: #94a3b8;  /* 次要文字 */
  --text-muted: #64748b;      /* 占位符 / 空值 */
  
  /* 语义色（状态 chip）*/
  --border: #1e293b;
}
```

### 组件复用策略

**`pages/shared.css`** — 跨工作区的表格、chip、进度条样式，避免三个页面各写一遍：

- `.data-table` — 表格基础样式（边框、padding、hover）
- `.status-chip` — 圆角 pill，背景色由内联 style 传入（不同状态不同色）
- `.progress-bar-container` / `.progress-bar` — 任务进度条（百分比 + 渐变）
- `.stat-card` — 统计卡片（上传中 / 已发布 / 失败）

**页面独有样式** — `SysOpsPage.css` 的 modal 样式、LoginPage.css 的居中布局，不放 shared

### 响应式原则

- 统计卡用 `grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))`，自动折行
- 标题用 `clamp(1.5rem, 2vw, 2rem)`，小屏不溢出
- 表格横向滚动（`overflow-x: auto`），不压缩列宽

## 错误处理

### API 调用统一封装

```typescript
// client.ts
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  const payload = await response.json() as Envelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(
      payload.error?.code ?? "UNKNOWN",
      payload.error?.message ?? "请求失败",
      response.status,
    );
  }
  return payload.data as T;
}
```

### 页面级错误展示

```typescript
// TasksPage.tsx
try {
  const { items } = await fetchTasks();
  setTasks(items);
} catch (e) {
  message.error(e instanceof Error ? e.message : '加载任务失败');
}
```

### 401 处理（登录过期）

当前未实现自动跳转，token 过期时用户会看到所有页面都报 401。后续 change 可在 `client.ts` 的 `get()` 里捕获 401 并清空 localStorage + 触发 App 重渲染。

## 性能考量

### 轮询节流

- **Task / Episode 列表** — 10s / 5s 间隔，离开页面时 `clearInterval`（`useEffect` 的 cleanup）
- **Agent 状态** — 5s 间隔（心跳本身是 15s，轮询比它快才能及时发现离线）

### 构建优化

```bash
npm run build
# dist/assets/index-7QamDNvQ.js  936.64 kB │ gzip: 295.83 kB
# ⚠ warning: chunks exceed 500 kB
```

Vite 警告单 chunk 过大（Ant Design + React 打在一起）。原型阶段可接受，生产需 code splitting：

```typescript
// vite.config.ts (后续优化)
build: {
  rollupOptions: {
    output: {
      manualChunks: {
        'vendor': ['react', 'react-dom'],
        'antd': ['antd'],
      },
    },
  },
},
```

## 实现中追加的决策

### 阶段划分：展示层分组，不改状态机

用户提出的流程是「采集 → 解析 → 质检 → 完成」4 段。契约有 10 个状态，直接映射会吞掉信息，
最终做 5 段 + 脱轨态：

| 阶段 | 对应状态 |
|---|---|
| 采集 | `recording` / `uploading` / `uploaded` |
| 解析 | `processing`（4 个算子：预标注 / 质检 / 关键帧 / 异常） |
| 质检 | `verification_pending`（人工核验） |
| 标注 | `annotation_pending` / `annotation_review` |
| 完成 | `published` |
| 脱轨 | `failed` / `rejected` — 不属于任何阶段 |

比 4 段多出「标注」：质检通过后还有标注与标注审核两步，4 段会把这段吞掉。
脱轨态单独处理，因为线性进度条表达不了「死在第 2 格」—— Episode 只存当前状态，
拿不到历史轨迹，所以换一种画法而不是画一条全灰的条。

命名歧义已澄清：`quality` 是**自动算子**，在「解析」阶段就跑完；「质检」这一格指的是
**人工核验**（人看着自动质检报告做判断）。

阶段只是 `utils/stage.ts` 里的分组表，契约的 10 个状态没动。界面上大阶段一列、
子状态一列并存 —— 前者给一眼看清进度，后者保留精确位置。改状态机要动 contract，
改分组只动这一个文件。

### 上传完成后谁负责推进到 processing

`uploaded → processing` 原本无人执行（见 proposal 的缺陷 1）。三个选项：

| 方案 | 语义 | 代价 |
|---|---|---|
| **A. Platform 发事件时自己推进** | 事件投递即视为进入处理 | 状态略微超前于实际开工 |
| B. Scheduler 先调接口声明开工 | 最准 | 多一次 HTTP 往返 + 新增路由 |
| C. 放宽状态机允许直接跳 | — | `processing` 失去意义，UI 上「解析」阶段永不可见 |

**选 A**，用户确认。理由是下一阶段要接 Celery，届时 worker 的开工时机由 Celery 管理，
Platform 这边「发出即处理中」的语义正好对得上；B 方案的那次往返在 Celery 下会变成冗余。

副作用：`mark_uploaded` 的重放判断要从「已是 `uploaded`」改成「不是 `uploading`」——
Agent 恢复流程会补发上传回调，只认 `uploaded` 会让补发撞上非法迁移。这一点由
`tests/test_uploaded_enters_processing.py` 守住。

### 数据库时间戳必须带时区

SQLite 没有原生 datetime，`DateTime(timezone=True)` 对它是空操作。新增 `UtcDateTime`
列类型在边界收口：写入时 naive 视为 UTC、aware 换算到 UTC，读出时补回 tzinfo。
库里始终是 UTC，时区转换只发生在展示层，前端显式指定 `Asia/Shanghai` 而非跟随浏览器 ——
同一条数据在任何机器上显示同一时刻。

## 遗留项

### 权限细化

当前只校验 role（admin / recorder / sysops），未隔离数据：

- Recorder A 能看到 Recorder B 的 Episode
- Agent X 能看到 Agent Y 的任务

生产需在 `/episodes` / `/tasks` 加 `?agent_id=` / `?recorder_id=` 过滤，后端据 JWT 的 `sub` 限制查询范围。
