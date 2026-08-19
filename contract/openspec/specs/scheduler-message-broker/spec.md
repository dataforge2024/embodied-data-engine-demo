# scheduler-message-broker Specification

## Purpose
TBD - created by archiving change scheduler-celery-rabbitmq. Update Purpose after archive.
## Requirements
### Requirement: 消息后端可切换

系统 SHALL 支持文件队列与 RabbitMQ 两种后端，由配置选择，调用方代码不因切换而改动。

#### Scenario: 默认后端

- **WHEN** 未指定队列后端
- **THEN** 使用文件队列，不需要任何外部服务

#### Scenario: 切到 RabbitMQ

- **WHEN** 配置指定 RabbitMQ
- **THEN** 发布与消费都经 broker
- **AND** 发布方与消费方的调用代码与文件队列模式完全相同

### Requirement: AMQP 拓扑取自契约

exchange、队列名、绑定关系 SHALL 全部来自契约的事件注册表，不在代码或部署配置里重复声明。

#### Scenario: 声明拓扑

- **WHEN** 系统初始化消息拓扑
- **THEN** exchange 名、队列名、绑定的 routing_key 均取自契约
- **AND** 代码中不硬编码这些值

#### Scenario: 发布方不知道消费队列

- **WHEN** 发布方投递一条事件
- **THEN** 它只提供 routing_key，投递目标由 exchange 的绑定决定

### Requirement: 不合契约的消息进死信

消费方 SHALL 在把消息交给任务执行层之前完成契约校验。

#### Scenario: payload 不合契约

- **WHEN** 收到的消息无法通过契约模型校验
- **THEN** 该消息进死信，不进入任务执行层
- **AND** 不消耗重试预算 —— 重试一条格式错误的消息永远不会成功

#### Scenario: 处理失败重试耗尽

- **WHEN** 某事件的处理失败次数超过契约为它声明的上限
- **THEN** 该消息进死信

### Requirement: 处理必须重跑无害

消息投递是至少一次语义。系统 SHALL NOT 维护外部去重表，每个事件处理逻辑 SHALL 满足
重复执行与执行一次结果相同。

#### Scenario: 同一事件被投递两次

- **WHEN** 同一 event_id 的消息被消费两次
- **THEN** 最终状态与只消费一次相同
- **AND** 不产生重复的状态变更或重复计数

#### Scenario: 新增处理逻辑

- **WHEN** 为某事件新增或修改处理逻辑
- **THEN** 该逻辑必须自行保证重跑无害

### Requirement: 每个订阅的事件都有处理归属

worker SHALL NOT 确认一条它没有实际处理的消息。订阅了却无人处理的事件是静默丢弃。

#### Scenario: 已订阅但未实现处理

- **WHEN** 某队列订阅的 routing_key 没有对应的处理逻辑
- **THEN** 日志以警告级别指出该事件未实现处理，而不是静默确认

#### Scenario: 订阅关系取自契约

- **WHEN** 某队列的 worker 启动
- **THEN** 其订阅集合由契约的「队列 → 事件」映射查出，代码中不硬编码

