## ADDED Requirements

### Requirement: Tool 复用 Platform 的用户体系登录

Tool SHALL 通过 Platform 现有的登录端点取得凭据，SHALL NOT 自建认证机制或使用硬编码身份。

#### Scenario: 登录成功

- **WHEN** 操作员在 Tool 输入有效的用户名与密码
- **THEN** 取得访问凭据并进入工作台
- **AND** 后续请求携带该凭据

#### Scenario: 未登录时不发业务请求

- **WHEN** 尚未登录
- **THEN** Tool 展示登录入口，不向业务端点发请求

#### Scenario: 凭据过期

- **WHEN** 凭据过期导致业务请求被拒
- **THEN** Tool 回到登录入口
- **AND** 不把过期凭据反复重试

#### Scenario: 身份来自登录结果

- **WHEN** 提交任一人工环节的操作
- **THEN** 操作人取自登录用户的真实标识，而非前端写死的占位值

### Requirement: 三个工作台各自加载对应队列

每个工作台 SHALL 只加载处于其前置状态的 Episode，队列内容取自 Platform 而非前端筛选。

#### Scenario: 队列内容

- **WHEN** 操作员打开某个工作台
- **THEN** 列表只含处于该环节前置状态的 Episode

#### Scenario: 操作后队列刷新

- **WHEN** 某条 Episode 的操作提交成功
- **THEN** 它从当前队列消失
- **AND** 出现在下一环节的队列里（若下一环节是人工环节）

#### Scenario: 送标处理中的不出现在标注队列

- **WHEN** 某条 Episode 正处于送标处理态
- **THEN** 标注队列里没有它

### Requirement: 标注可编辑分段与填写描述

标注工作台 SHALL 允许操作员编辑分段的时间边界、动作标签与文字描述，并为整条 Episode
填写备注。

#### Scenario: 基于预标注修改

- **WHEN** 操作员打开一条带预标注分段的 Episode
- **THEN** 已有分段可见且可编辑
- **AND** 修改后的分段来源标记为人工

#### Scenario: 提交标注

- **WHEN** 操作员提交至少一个分段
- **THEN** 分段与备注一并落库
- **AND** Episode 进入审核环节

#### Scenario: 空标注被拒

- **WHEN** 操作员在没有任何分段的情况下提交
- **THEN** 提交被拒绝并给出可读提示

### Requirement: 审核可见标注内容并作出裁决

审核工作台 SHALL 展示待审核的标注内容，并支持通过与退回两种裁决。

#### Scenario: 审核通过

- **WHEN** 审核人对某条标注作出通过裁决
- **THEN** Episode 进入已发布态

#### Scenario: 审核退回

- **WHEN** 审核人作出退回裁决并给出原因
- **THEN** Episode 回到待标注态
- **AND** 退回原因可被标注人看到

#### Scenario: 审核前可查看标注

- **WHEN** 审核人打开一条待审核的 Episode
- **THEN** 能看到标注人提交的分段与备注
