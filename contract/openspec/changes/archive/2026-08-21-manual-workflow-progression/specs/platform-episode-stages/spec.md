## MODIFIED Requirements

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
