## ADDED Requirements

### Requirement: 五步人工推进各有前置状态

工作流的每一步 SHALL 校验 Episode 处于该步骤的前置状态，不满足时拒绝操作并返回冲突，
而不是静默改写状态。

#### Scenario: 按顺序推进

- **WHEN** Episode 处于某步骤的前置状态且操作者具备权限
- **THEN** 该步骤的操作被接受
- **AND** Episode 推进到该步骤声明的目标状态

#### Scenario: 跳步或重复操作

- **WHEN** 对不处于前置状态的 Episode 执行某步骤
- **THEN** 操作被拒绝并返回冲突（409）
- **AND** Episode 的状态与产物不被改动

#### Scenario: 终态不接受任何操作

- **WHEN** Episode 已到终态
- **THEN** 五个步骤的操作全部被拒绝

### Requirement: 质检通过后先进送标处理

质检通过 SHALL NOT 直接把 Episode 送进标注队列。中间必须经过一个可见的送标处理阶段，
使「正在准备标注数据」与「已可标注」两种情形在状态上可区分。

#### Scenario: 质检通过

- **WHEN** 核验人提交通过裁决
- **THEN** Episode 进入送标处理态
- **AND** 该 Episode 不出现在标注队列里

#### Scenario: 送标处理完成

- **WHEN** 送标处理成功结束
- **THEN** Episode 进入标注队列
- **AND** 标注人能在队列中看到它

#### Scenario: 送标处理失败

- **WHEN** 送标处理过程中发生错误
- **THEN** Episode 落入失败态并记录原因
- **AND** 不会静默留在质检队列里让人以为提交未生效

#### Scenario: 质检打回

- **WHEN** 核验人提交打回裁决
- **THEN** Episode 直接终止，不进入送标处理

### Requirement: 人工环节的操作者身份必须真实

质检、标注、审核的操作人字段 SHALL 记录真实的用户标识，而非前端硬编码的占位字符串。

#### Scenario: 记录操作人

- **WHEN** 任一人工环节的操作被提交
- **THEN** 落库的操作人字段取自当前登录用户的标识
- **AND** 该标识对应一个真实存在的用户

### Requirement: 三个人工环节统一权限

质检、标注、审核 SHALL 接受同一个业务角色，管理员亦可执行全部环节。

#### Scenario: 业务角色操作

- **WHEN** 具备标注角色的用户访问三个人工环节中的任一个
- **THEN** 操作被允许

#### Scenario: 管理员操作

- **WHEN** 管理员访问三个人工环节中的任一个
- **THEN** 操作被允许

#### Scenario: 无关角色被拒

- **WHEN** 既非管理员也不具备标注角色的用户尝试操作
- **THEN** 请求被拒绝（403）

### Requirement: 新增状态必须落在阶段视图内

Episode 的每个状态 SHALL 能被展示层归入某个阶段或明确的脱轨态，不允许出现无归属的状态。

#### Scenario: 送标处理态的阶段归属

- **WHEN** 展示层对处于送标处理态的 Episode 求阶段
- **THEN** 返回一个确定的阶段，而不是空值或未知
