# robotdatahub-tool

质检与标注前端。React 18 + TypeScript，无自有后端 —— 所有数据经 Platform REST API。

## 我依赖 contract 的什么

通过 `@contract` 别名引用 contract 生成的 `types/contract.ts`：

| 契约项 | 用途 |
|---|---|
| `Episode` / `Segment` / `SensorStream` | 回放与时间轴的数据结构 |
| `Annotation` / `AnnotationSubmit` | 标注提交 |
| `VerifyResult` / `ReviewResult` | 核验与审核裁决 |
| `EpisodeStatus` / `canTransition` / `isTerminal` | 按状态禁用非法操作按钮，不自己硬编码状态规则 |
| `CONTRACT_VERSION` | 启动时可与后端 `/health` 比对 |

**不重写一份 interface** —— 后端改字段这里会编译报错，而不是运行期拿到 `undefined`。

```ts
// vite.config.ts / tsconfig.json 里的别名
'@contract' → '../contract/types/contract.ts'
```

真实拆仓后改为 npm 包引用（`@robotdatahub/contract`），import 语句不动。

## 我暴露什么

不暴露 API。纯前端，产物是静态资源。

## 我参与哪几条交互

| # | 角色 | 实现位置 |
|---|---|---|
| ④ | 调 Platform REST 做核验/标注/审核 | `src/api/client.ts` |

## 三个工作台

| 页面 | 环节 | Episode 状态流转 |
|---|---|---|
| `VerifyPage` | 核验：判断数据本身可用性 | `verification_pending` → `annotation_pending` / `rejected` |
| `AnnotatePage` | 标注：编辑动作分段与描述 | `annotation_pending` → `annotation_review` |
| `ReviewPage` | 审核：判断标注质量 | `annotation_review` → `published` / `annotation_pending` |

**审核「退回」≠ 核验「打回」**：前者让标注重做（回 `annotation_pending`），
后者把 Episode 判死（`rejected` 终态）。UI 文案必须区分开，否则操作人会误判。

## 两个技术要点

**多视角同步回放**（`components/player/SyncController.ts`）——
各路相机流的 `start_offset_ms` 不同，且 `<video>` 的 seek 是异步的。做法是维护一个
**逻辑主时钟**，各路 video 只是从动者。不用某一路 video 的 `currentTime` 当基准，
否则该路卡顿会把其余路一起带偏。漂移超过 80ms 才强制 seek 纠偏 —— 每帧都 seek 会卡。

**分段编辑**（`components/timeline/segmentMath.ts`）——
切分/合并/拖拽边界全是纯函数，返回新数组而非原地修改，因此撤销重做只需保存历史快照。
校验规则与后端 `AnnotationSubmit` 一致（不重叠、不越界、不短于最小时长），前端提前拦住
避免白跑一次请求。

预标注分段是标注的**起点**而非成品：标注人在算子结果上改，人工修改后 `source` 置 null。

## 运行

```bash
pnpm install
pnpm dev          # http://localhost:5174，API 代理到 127.0.0.1:8000
pnpm typecheck
```
