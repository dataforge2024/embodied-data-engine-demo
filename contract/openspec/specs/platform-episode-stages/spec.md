# platform-episode-stages Specification

## Purpose
定义把契约的 11 个 Episode 状态在展示层归入 6 个大阶段的规则。
阶段是展示层分组，不改变状态机；大阶段与精确子状态并存。
`failed` / `rejected` 不映射到任何阶段 —— 线性进度条无法表达「死在第几格」。
## Requirements
### Requirement: 阶段视图

控制台 SHALL 把 Episode 的 11 个状态在展示层归入 6 个大阶段，同时保留精确子状态。
阶段是展示层分组，不改变契约状态机。

阶段命名 SHALL 标明该阶段在等人还是在等系统 —— 六个阶段严格交替（人工 → 自动 → 人工 →
自动 → 人工 → 完成），使看进度条即可判断下一步该谁动。

#### Scenario: 阶段与状态的对应

- **WHEN** 界面渲染某条 Episode 的阶段
- **THEN** `recording` / `uploading` / `uploaded` 归入「采集人工作业」
- **AND** `processing` 归入「采集自动解析」
- **AND** `verification_pending` 归入「采集人工质检」
- **AND** `annotation_processing` 归入「标注自动送标」
- **AND** `annotation_pending` / `annotation_review` 归入「标注人工作业」
- **AND** `published` 归入「完成」

#### Scenario: 大阶段与子状态并存

- **WHEN** 用户查看 Episode 列表
- **THEN** 同时看到所处大阶段与精确子状态 —— 前者给进度概览，后者给确切位置

#### Scenario: 送标处理独占一格

- **WHEN** 某条 Episode 处于送标处理态
- **THEN** 它落在「标注自动送标」这一格，与待标注态不在同一格
- **AND** 界面能区分「在等系统跑完」与「在等人来标」

#### Scenario: 审核退回不让进度倒退

- **WHEN** 审核人退回一条标注，Episode 由标注审核态回到待标注态
- **THEN** 阶段仍是「标注人工作业」，进度条不后退
- **AND** 子状态由「标注审核」变为「待标注」

#### Scenario: 已走过的阶段可区分

- **WHEN** Episode 处于某个阶段
- **THEN** 其之前的阶段显示为已完成，当前阶段被强调，之后的阶段显示为未到达

### Requirement: 脱轨态不映射到阶段

`failed` 与 `rejected` SHALL NOT 归入任何阶段，界面用区别于进度条的画法呈现。

#### Scenario: Episode 失败

- **WHEN** Episode 处于 `failed`
- **THEN** 不显示阶段进度，而是明确标示流程中断

#### Scenario: Episode 被打回

- **WHEN** Episode 处于 `rejected`
- **THEN** 同样标示为流程中断，并与失败可区分

#### Scenario: 为何不画在进度条上

- **WHEN** 需要表达「死在第几个阶段」
- **THEN** 不做此表达 —— Episode 只存当前状态，拿不到历史轨迹，线性进度条无法承载

### Requirement: 阶段汇总

任务详情 SHALL 按阶段汇总其子任务数量。

#### Scenario: 查看任务详情

- **WHEN** 用户打开任务详情页
- **THEN** 看到每个阶段各有多少条子任务

#### Scenario: 存在脱轨的子任务

- **WHEN** 该任务下有 `failed` 或 `rejected` 的子任务
- **THEN** 单独呈现其数量，不混入任何阶段计数

#### Scenario: 筛选不影响汇总

- **WHEN** 用户按子状态筛选子任务列表
- **THEN** 阶段汇总仍基于该任务的全量数据，不随筛选变化

