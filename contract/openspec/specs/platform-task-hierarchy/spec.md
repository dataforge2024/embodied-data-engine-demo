# platform-task-hierarchy Specification

## Purpose
定义任务与子任务的父子关系在控制台的表达：任务是父，Episode 是子，
一次采集上传即建一条子任务。同时规定任务进度以**已采集**计算而非已发布 ——
后者要走完人工环节，只看它会让刚上传的数据在界面上毫无反应。

## Requirements
### Requirement: 任务与子任务的父子视图

控制台 SHALL 把 Episode 表达为所属任务的子任务：任务是父，一次采集上传即建一条子任务。

#### Scenario: 从任务进入子任务列表

- **WHEN** 用户在任务列表点击任务名或「查看子任务」
- **THEN** 进入该任务的详情页，只列出属于它的 Episode
- **AND** 页面提供返回任务列表的入口

#### Scenario: 子任务列表不重复显示所属任务

- **WHEN** 在任务详情页查看子任务
- **THEN** 不显示「所属任务」列 —— 整张表同属一个任务，该列无信息量

#### Scenario: 任务还没有子任务

- **WHEN** 任务已创建但 Agent 尚未上传任何文件
- **THEN** 提示该任务还没有子任务，并说明上传一个文件即建一条

#### Scenario: 跨任务查看历史

- **WHEN** 用户打开采集记录页
- **THEN** 看到所有任务的 Episode
- **AND** 可按任务与子状态筛选
- **AND** 「所属任务」列显示任务名而非 ID

### Requirement: 任务进度反映已采集数

任务进度 SHALL 以已采集条数计算，而非已发布条数。

#### Scenario: 刚上传完一条

- **WHEN** 一条 Episode 完成上传回调
- **THEN** 任务进度立即 +1，无需等待后续人工环节

#### Scenario: 已发布数单独呈现

- **WHEN** 用户查看任务列表
- **THEN** 已发布条数作为独立信息展示
- **AND** 说明它要走完解析、核验、标注、审核才计入

### Requirement: 按任务过滤 Episode

Platform SHALL 支持按 task_id 过滤 Episode 查询。

#### Scenario: 指定 task_id

- **WHEN** 调用方在 `GET /episodes` 传 `task_id`
- **THEN** 只返回该任务下的 Episode

#### Scenario: 与状态过滤同时使用

- **WHEN** 同时传 `task_id` 与 `status`
- **THEN** 两个条件同时生效

### Requirement: 采集员归属可追溯

每条 Episode SHALL 记录采集员身份，且该身份由 Platform 认定。

#### Scenario: Agent 登记 Episode

- **WHEN** Agent 调用 `POST /episodes`
- **THEN** `recorded_by` 取自调用方 JWT 而非请求体 —— 不采信 Agent 上报，避免冒名

#### Scenario: 界面显示采集员

- **WHEN** 用户查看 Episode 列表
- **THEN** 显示采集员的展示名而非 user_id

#### Scenario: 用户信息查不到

- **WHEN** 用户列表拉取失败或该 user_id 不存在
- **THEN** 退化显示 ID 前缀，不影响其余信息呈现

