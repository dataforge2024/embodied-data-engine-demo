# platform-dataset-export Specification

## Purpose
TBD - created by archiving change manual-workflow-progression. Update Purpose after archive.
## Requirements
### Requirement: 导出只接受已发布的 Episode

训练集构建 SHALL 只纳入已走完人工链路的 Episode，SHALL NOT 纳入仍在流程中或已终止的。

#### Scenario: 全部已发布

- **WHEN** 请求构建的 Episode 全部处于已发布态
- **THEN** 请求被受理

#### Scenario: 含未发布的

- **WHEN** 请求里含任何未到已发布态的 Episode
- **THEN** 请求被拒绝并指出哪几条不合格
- **AND** 不产生半成品的构建记录

### Requirement: 构建状态可查询

构建是异步的，因此 SHALL 提供查询入口，让发起人不必翻日志就能知道构建是否完成。

#### Scenario: 查询进行中的构建

- **WHEN** 构建已受理但尚未完成
- **THEN** 查询返回进行中状态

#### Scenario: 查询已完成的构建

- **WHEN** 构建已完成
- **THEN** 查询返回完成状态与产物位置
- **AND** 返回纳入的 Episode 数量

#### Scenario: 查询失败的构建

- **WHEN** 构建过程中出错
- **THEN** 查询返回失败状态与原因

#### Scenario: 查询不存在的构建

- **WHEN** 查询一个不存在的构建标识
- **THEN** 返回未找到（404）

### Requirement: 构建产出可核对的清单

构建 SHALL 落地一份清单，记录纳入了什么、每条的最终分段、以及算子产物的位置，
使导出结果可被人工核对。

#### Scenario: 清单内容

- **WHEN** 构建完成
- **THEN** 清单含每条 Episode 的标识与最终分段
- **AND** 含算子产物的对象键
- **AND** 含导出格式与发起人

#### Scenario: 分段取人工最终版

- **WHEN** 某条 Episode 的分段被人工修改过
- **THEN** 清单里记录的是人工修改后的版本，而非算子的预标注版本

### Requirement: 导出由管理员发起

导出入口 SHALL 授予实际存在账号的角色，避免声明了权限却无人能调。

#### Scenario: 管理员导出

- **WHEN** 管理员发起构建
- **THEN** 请求被受理

#### Scenario: 无权角色被拒

- **WHEN** 不具备导出权限的用户发起构建
- **THEN** 请求被拒绝（403）

