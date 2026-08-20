# 人工工作流推进设计

## 1. 送标处理为什么要新增状态

讨论起点：质检通过后「接一个送标处理（走算子）」。现状状态机里
`verification_pending → annotation_pending` 是直连的，中间没有环节。

| 方案 | 状态机改动 | 代价 |
|---|---|---|
| 甲 复用 `PROCESSING` | 加 2 条边 | `PROCESSING` 出现两个出口，回调无法判断该去哪 |
| **乙 新增 `ANNOTATION_PROCESSING`** | 加 1 状态 + 3 条边 | 契约动一次，波及 6 个模块 |
| 丙 质检提交时同步跑 | 零改动 | 请求挂住；失败时静默卡在 `verification_pending` |

**选乙。**

### 为什么不是甲

`PROCESSING` 现在只有一个出口 `verification_pending`，`finish_processing()` 据此推进。
若再加一条到 `annotation_pending`，同一个回调就有两种含义：

```
processing ──▶ verification_pending   （解析阶段完成）
           └─▶ annotation_pending     （送标阶段完成）
```

Scheduler 的 `algo-result` 回调里没有「我在哪个阶段」这个信息，得额外加字段区分。
而两个阶段的语义本来就不同：一个是「解析完等人看」，一个是「送标完等人标」。
用同一个状态表达，UI 上也分不清「解析中」和「送标中」。

### 为什么不是丙

同步跑意味着质检提交这个 HTTP 请求要等算子跑完。本地子进程几秒，真接 K8s Job 是几分钟。
更要紧的是失败路径：算子挂了，Episode 静默留在 `verification_pending`，
点过质检的人以为提交成功了，实际什么都没发生。这与 `platform-pipeline-integrity` 里
「`uploaded → processing` 必须有归属方」踩的是同一个坑。

### 状态机改动

```
                    ┌─────────────────────────┐
                    │  verification_pending   │
                    └─────────────────────────┘
                         │              │
              质检通过    │              │  质检打回
                         ▼              ▼
            ┌─────────────────────┐  ┌──────────┐
            │annotation_processing│  │ rejected │
            └─────────────────────┘  └──────────┘
                    │         │
         送标完成    │         │  算子失败
                    ▼         ▼
       ┌────────────────────┐ ┌────────┐
       │ annotation_pending │ │ failed │
       └────────────────────┘ └────────┘
```

`verification_pending` 的出边由 `{annotation_pending, rejected}` 改为
`{annotation_processing, rejected}` —— 这是 BREAKING 的实质：老的那条边被移除，
任何依赖它的代码会撞 `InvalidTransitionError`。

## 2. 送标处理为什么先不跑算子

4 个算子（`preannotate` / `quality` / `keyframe` / `anomaly`）在**解析阶段已经全跑过一遍**，
`AnnotatePage` 现在加载的 `episode.segments` 正是 `preannotate` 那时的产物。

所以「送标再走算子」要跑什么，并不自明。三种可能：

| | 含义 | 前置条件 |
|---|---|---|
| A | 重跑 `preannotate`，用质检确认过的 topic 产出更准的分段 | 得先明确「质检信息如何影响预标注」 |
| B | 不跑算子，只是一个准备标注数据的异步环节 | 无 |
| C | 跑目前不存在的新算子 | 得先定义算子 |

**本变更按 B 落地**：`annotation_processing` 这个状态本身是有价值的（送标中可见、失败有去处、
可重试），而它内部跑什么可以后填。状态机改动是硬骨头且波及全局，算子内容是局部的 ——
先把硬骨头啃掉，避免为了等算子定义而把状态机改动也一起拖着。

落点明确：`EpisodePipeline` 新增一个送标处理入口，本阶段只做数据准备与状态推进。
将来要接算子时，改的是这个方法内部，状态机不用再动。

## 3. 三个人工环节合并到 annotator

现状三个端点各要一个角色：

```
POST /verifications  → VERIFIER
POST /annotations    → ANNOTATOR
POST /reviews        → REVIEWER
```

单个 `annotator` 账号进不去质检和审核。三条路：

| 方案 | 做法 | 代价 |
|---|---|---|
| 甲 种多角色账号 | 一个账号带三个角色，端点不动 | 端点仍声明分工，但演示账号把它绕过了 |
| **乙 收敛端点要求** | 三个端点都改 `ANNOTATOR` | 权限边界真的放宽了 |
| 丙 用 admin | 零改动（`ADMIN` 已通配） | 演示时体现不出角色 |

**选乙**，因为它是**诚实的**：如果实际上不打算区分这三个角色，让端点继续声明
`VERIFIER` / `REVIEWER` 却用一个多角色账号绕过，代码里的权限声明就成了摆设 ——
下一个读代码的人会以为有分工。

代价要写明：**审核与被审核变成同一个角色**。标注的人能审自己的标注，这在真实场景里
是要避免的（四眼原则）。真要分工时改回去，那时端点要求与账号角色一起调整。

`ADMIN` 的通配（`dependencies.py:189`）不动，所以「admin 也可以质检 / 标注 / 审核」
无需额外改动，它本来就成立。

### 三个账号，四个死角色

落地后只有三个账号可登录：

| 账号 | 角色 | 能做什么 |
|---|---|---|
| `admin` | `ADMIN` | 通配一切（建任务、分派、三个人工环节、导出） |
| `recorder` | `RECORDER` | 采集记录、运维监控 |
| `annotator` | `ANNOTATOR` | 质检、标注、审核 |

密码统一取 `seed.py` 的默认值。

枚举里另外四个角色**无账号且不再被引用**：`VERIFIER` / `REVIEWER`（端点收敛后失去引用）、
`LAB`（导出改归 `ADMIN`）、`SYSOPS`（本来就是死枚举 —— 全仓库 grep 只命中枚举定义本身，
`/sysops/*` 端点与「运维监控」工作区要的都是 `ADMIN`+`RECORDER`，同名而非同物）。

**不删这四个枚举值。** 删要动契约，而契约是 6 个模块的共同依赖，为清理死值付这个代价不值。
代价是代码里留着四个永远匹配不到任何用户的角色 —— 记在这里，避免下一个人误以为
`SYSOPS` 在保护运维页。

`RECORDER` 进不了 Tool（三个人工环节只认 `ANNOTATOR` 与 `ADMIN`）。若实际是同一批人既采集
又标注，给 `recorder` 账号加上 `ANNOTATOR` 角色即可 —— `User.roles` 是列表，支持多角色。

## 4a. 六个阶段的划分

阶段是展示层分组，与状态机是两件事。本变更把 5 阶段改为 6 阶段：

```
① 采集人工作业   recording / uploading / uploaded     人
② 采集自动解析   processing                           系统
③ 采集人工质检   verification_pending                 人
④ 标注自动送标   annotation_processing                系统
⑤ 标注人工作业   annotation_pending / annotation_review  人
⑥ 完成          published                            —
```

**划分依据是「谁在动」**，并写进阶段名。六格严格交替（人 → 系统 → 人 → 系统 → 人 → 完成），
所以看进度条就知道下一步是等人还是等系统 —— 这是原来那套平铺命名（采集/解析/质检/标注/完成）
没有的性质。

两个合并/拆分的决定：

**④ 送标独占一格，不与待标注合并。** 若合并，进度条上「算子在跑」与「人可以开始标了」
落在同一格，看不出区别；而这两种情形的处置完全不同（一个等着，一个去干活）。

**⑤ 标注与审核合并一格。** 它们同属「人在处理标注这件事」，且审核退回会回到待标注 ——
两者之间有个内部循环：

```
⑤ 标注人工作业
   ┌────────────────────────────────────────┐
   │ annotation_pending ⇄ annotation_review │   ← 退回重做的循环藏在格内
   └────────────────────────────────────────┘
```

把循环藏在格内，退回时进度条不后退，只有子状态变。若拆成两格，审核退回会让进度条
从第⑥格退回第⑤格 —— 用户看到进度倒退，而实际上这是正常流程的一部分。

`recording` / `uploading` / `uploaded` 三个状态挤在①里是同样的处理：格内的细节由子状态
chip 表达，阶段只管大进度。

显示上要注意：六个标签里五个是六字，⑥是两字。`StageBar` 的 compact 模式只画点不写字，
不受影响；完整模式六个六字标签横排会挤，可能需要两行排（前缀小字 + 动作大字）或只显示
后半段。这是实现细节，不影响阶段划分本身。

## 4. Tool 的认证形态

复用 Platform 的 `POST /auth/login`，不新增认证机制。Tool 与 Platform 是两个独立前端，
但共用同一套用户体系与 JWT —— 契约里的 `TokenResponse` 已经够用。

| | 做法 | 适合 |
|---|---|---|
| **甲 Tool 自己的登录页** | 输用户名密码 → 存 token | 两批人、独立部署 |
| 乙 接 Platform 的 token | 从 URL / localStorage 取 | 同一批人同一浏览器 |
| 丙 单账号写进 env | 免登录 | 纯演示，不追求身份真实 |

**选甲。** 用户此前明确 Platform 与 Tool 是两批人用，那 Tool 需要自己的登录入口。
乙依赖同源 localStorage，跨域名部署就失效；丙让 `verified_by` 又变回假身份，
与本变更要修的问题之一冲突。

JWT 有 TTL（默认 1 小时）。Tool 是浏览器应用，401 时回登录页即可 —— 不需要像 Agent
那样自动重登（Agent 是无人值守的常驻进程，那个修复见 `fix(agent)` commit）。

## 5. 导出做到哪一步

现状：`POST /datasets/build` 在、发得出事件，但 Celery task 是 stub
（返回 `not_implemented`），契约里没有 `Dataset` 模型，构建状态查不到。

**本变更落地 manifest，不做真实格式转换。**

```
datasets/<dataset_id>/manifest.json
  ├─ episode 清单与状态
  ├─ 每条的 segments（人工标注后的最终版）
  └─ 算子产物的 object_key
```

理由：演示时点「导出」要有东西可看，否则这一步只是日志里一行 warning。而 lerobot / rlds
的真实格式规范本身工作量不小，原 `agent-task-directory-upload` 的 tasks.md 已经把它
划为单开 change —— 那个判断仍然有效。

`GET /datasets/{id}` 是新增端点：没有它，构建完成与否只能翻日志。

导出权限从 `Role.LAB` 改到 `ADMIN`：`lab` 角色既无账号也无界面，保留它意味着这个端点
永远调不通。

## 6. 五步都靠人推进

不做自动推进。每一步的前置状态由 `assert_actionable` 守卫：

| 步骤 | 前置状态 | 操作者 | 结果 |
|---|---|---|---|
| 质检 | `verification_pending` | annotator / admin | 通过→送标处理；打回→rejected |
| 送标 | `annotation_processing` | 系统（异步） | 完成→annotation_pending；失败→failed |
| 标注 | `annotation_pending` | annotator / admin | 提交→annotation_review |
| 审核 | `annotation_review` | annotator / admin | 通过→published；退回→annotation_pending |
| 导出 | `published` | admin | 产出 manifest |

点错顺序返回 409 而不是静默改状态 —— 这与 `episode_lifecycle` 现有的守卫一致。
第 2 步是唯一非人工的，因为它是异步处理；但触发它的是第 1 步的人工操作。

## 已知不足（POC 阶段接受）

**审核与被审核同角色。** 见第 3 节，四眼原则被放弃了。真实场景下标注员不应审自己的标注。

**送标处理不跑算子。** 见第 2 节，状态与环节先落地，算子内容待定。这意味着这一步在
演示时看起来只是「转一下状态」—— 如果演示需要它有可见的耗时与产物，得先定 A 方案。

**manifest 不是可训练的数据集。** 它是清单而非打包产物，下游拿它不能直接喂给训练框架。

## 后续

- 送标处理接真实算子（前置：明确跑什么、质检信息如何影响预标注）
- 角色分工恢复（前置：确定实际是否分人）
- lerobot / rlds 真实导出（单开 change）
