## Why

场景 3（质检 + 标注模拟）目前**在门口就断了**。Tool 三个页面的代码都在，但：

```
tool/src 里 login() / setAccessToken() 调用次数 = 0
        ↓
请求永远不带 Authorization
        ↓
platform/routes/review.py 三个队列端点都要角色
        ↓
全部 401 —— 三个页面拿不到任何数据
```

第二层问题：`tool/src/App.tsx:14` 的 `currentUser = 'tool-operator'` 是硬编码字符串，被当
`verified_by` / `reviewed_by` 落库，而契约说那是 `user_id`。就算认证通了，身份也是个不存在的人。

第三层：`seed.py` 只种了 `admin` / `recorder` 两个账号，契约里的 `verifier` / `annotator` /
`reviewer` 无账号可用。

结果是整条人工链路（质检 → 送标 → 标注 → 审核 → 导出）从未被人真正点过一遍。场景 1 与
场景 2 都已实测走通，场景 3 是三个场景里唯一还没验证过的一环，也是「靠人一步步推进」这个
演示诉求的落点。

## What Changes

**Tool 认证（当前的硬阻塞）**

- Tool 复用 Platform 现有的 `POST /auth/login`，不新增认证机制
- 三个人工环节统一用 `annotator` 角色；`admin` 因 `require_roles` 里的通配（`dependencies.py:189`）
  自然也能操作全部环节
- 落地后共三个可登录账号：`admin`（管理员，通配）/ `recorder`（采集员）/ `annotator`（标注员）。
  枚举里的 `verifier` / `reviewer` / `lab` / `sysops` 无账号且不再被引用，但**不删** ——
  删要动契约，为清理死值不值（见 design.md 第 3 节）
- `review.py` 三个端点的角色要求由 `VERIFIER` / `ANNOTATOR` / `REVIEWER` 收敛为 `ANNOTATOR`
  —— **BREAKING**（权限边界放宽；真要分工时需改回，理由记在 design.md）
- `seed.py` 新增 `annotator` demo 账号
- `currentUser` 由硬编码字符串改为登录后的真实 `user_id`

**送标处理（新增环节）**

- 新增 `EpisodeStatus.ANNOTATION_PROCESSING` —— **BREAKING**（契约状态机新增状态与三条边）
- 状态机：`verification_pending → annotation_processing → annotation_pending`，
  失败落 `failed`，取消/打回落 `rejected`
- 质检通过不再直接进标注队列，而是先进送标处理；处理完成后才进标注队列
- 本阶段送标处理**不重跑算子**：4 个算子已在解析阶段跑完，预标注分段就是那时的产物。
  这一步先落地为可见的异步环节（准备标注数据），跑什么算子留待明确后填充
  （见 design.md「送标处理为什么先不跑算子」）

**标注表单**

- `AnnotatePage` 现有时间轴编辑器保留，补一个简单表单：分段的 `action_label` /
  `description`、整条 Episode 的 `notes`
- 字段全部取自契约已有的 `Segment` 与 `AnnotationSubmit`，不新增契约字段

**导出数据集**

- 新增 `GET /datasets/{dataset_id}`，返回构建状态与产物清单
- 契约新增 `Dataset` 模型（当前 `schemas/` 下没有）
- `dataset.build_requested` 的 Celery task 由 stub 改为真的落地一份 `manifest.json`
  （episode 清单 + 分段 + 算子产物 object_key），不做 lerobot / rlds 真实格式转换
- 导出入口给 `admin`；`datasets.py` 现在要 `Role.LAB`，而 `lab` 无账号也无界面

**推进方式**

五步全部由人在界面上点，不做自动推进。每一步的前置状态由 `assert_actionable` 守卫，
点错顺序返回 409 而不是静默改状态。

## Capabilities

### New Capabilities

- `tool-operator-console`: Tool 的登录与身份、三个工作台的队列加载、标注表单、
  操作后的队列刷新
- `platform-dataset-export`: 训练集构建状态查询、manifest 产出、导出权限

### Modified Capabilities

- `platform-episode-stages`: 阶段由 5 个改为 6 个，容纳新增的 `annotation_processing`
  并按「谁在动」重新划分（人工 → 自动 → 人工 → 自动 → 人工 → 完成）。
  `STATUS_TO_STAGE` 覆盖的状态由 10 个变 11 个，见 design.md 第 4a 节

新增 capability `platform-manual-workflow` 承载五步推进的状态约束与权限要求。

## Impact

**契约（`contract/`）— 0.1.0 → 0.2.0**

| 项 | 变更 |
|---|---|
| `enums.py` | `EpisodeStatus` 新增 `ANNOTATION_PROCESSING` |
| `state_machine.py` | 新增 3 条边，`verification_pending` 的出边改向 |
| `schemas/` | 新增 `Dataset` 模型 |
| `openapi/platform.yaml` | 新增 `GET /datasets/{id}` |
| 生成物 | `types/contract.ts` 与 `events/*.json` 需重跑 `make contract-gen` |

状态机是 6 个模块的共同依赖，改动会传播到全部下游 —— 契约测试（状态机、事件注册表）
必须同步更新，`tool/` 与 `platform/web` 的状态标签映射也要补新状态。

**Platform（`platform/`）**

| 文件 | 变更 |
|---|---|
| `services/review.py` | 质检通过改为进 `annotation_processing`；新增送标完成的推进 |
| `api/routes/review.py` | 三个端点角色要求收敛为 `ANNOTATOR` |
| `api/routes/datasets.py` | 新增 `GET /{id}`；`POST /build` 权限改 `ADMIN` |
| `services/seed.py` | 新增 `annotator` 账号 |
| `web/src/components/EpisodeTable.tsx` | 新状态的标签与颜色 |
| `web/src/utils/stage.ts` | `STATUS_TO_STAGE` 补新状态 |

**Tool（`tool/`）**

| 文件 | 变更 |
|---|---|
| `App.tsx` | 新增登录页与 token 存储；`currentUser` 改真实 user_id |
| `api/client.ts` | 登录后 `setAccessToken`；401 时回登录页 |
| `pages/AnnotatePage.tsx` | 补标注表单字段 |
| `pages/VerifyPage.tsx` / `ReviewPage.tsx` | 提交人改真实 user_id |

**Scheduler（`scheduler/`）**

| 文件 | 变更 |
|---|---|
| `celery_app.py` | `build_dataset` 由 stub 改为产出 manifest |
| `pipelines/` | 新增送标处理的落点（本阶段不跑算子，留接口） |

**Testing（`testing/`）**

- `contract_checks/`：状态机新状态须在一致性校验中出现
- `e2e/`：现有 7 个用例中凡走 `verification_pending → annotation_pending` 的都会因
  中间多一跳而失败，需复核
- 新增：五步人工推进的端到端用例

## 不在本变更范围

- lerobot / rlds 真实格式转换（原 `agent-task-directory-upload` 已划为单开 change）
- 送标处理具体跑哪些算子（本变更只落地状态与环节）
- Tool 读 URL 参数做深链（`?episode=<id>&stage=verify` 已由 Platform 侧带上，Tool 侧读取另议）
- 角色分工细化（本变更故意把三个环节合并到 `annotator`）
- `agent-task-directory-upload` 遗留的 3 处行为缺陷（Agent 不回 ack、`TaskCancelFrame`
  零处理、`upload_progress` 读不到）—— 属场景 1，与本变更无依赖
