# platform-pipeline-integrity Specification

## Purpose
定义采集到解析这段链路的完整性约束：`uploaded → processing` 的归属方，
以及时间戳必须带时区标记。两者都曾因无人负责而静默出错 ——
前者让整条解析链路卡死，后者让前端时间差 8 小时。

## Requirements
### Requirement: 上传完成后自动进入处理态

Platform SHALL 在发出 `episode.uploaded` 后自行把 Episode 推进到 `processing`。
Scheduler 只上报结果、不改 Platform 状态，因此这一跳没有别的归属方。

#### Scenario: 上传回调到达

- **WHEN** Agent 调用 `POST /callbacks/upload-complete` 且 checksum 校验通过
- **THEN** Episode 落定为 `processing`
- **AND** `episode.uploaded` 事件已投递
- **AND** 上传产物（object_key、大小、时长、checksum）不被推进状态的动作冲掉

#### Scenario: Scheduler 回调结果

- **WHEN** Scheduler 跑完算子流水线并调用 `POST /callbacks/algo-result`
- **THEN** `processing → verification_pending` 是合法迁移，回调返回成功
- **AND** 算子产物（分段、关键帧、质检报告）落库

#### Scenario: Agent 补发上传回调

- **WHEN** Agent 的恢复流程重发同一条上传回调，而 Episode 已在 `processing` 或更后
- **THEN** Platform 视为已处理，返回现状而不抛非法迁移
- **AND** 不重复投递 `episode.uploaded`

#### Scenario: 生产代码不得依赖测试脚本补跳

- **WHEN** 检查谁执行 `uploaded → processing`
- **THEN** 该跳由 Platform 生产代码完成
- **AND** demo 与 e2e 脚本不得自行补这一跳 —— 那会掩盖生产链路的断裂

### Requirement: 数据库时间戳带时区标记

Platform SHALL 保证对外输出的时间戳带明确的 UTC 偏移。

#### Scenario: 读取已存储的时间

- **WHEN** 从数据库取出任何时间字段
- **THEN** 该值带 tzinfo，序列化后含偏移标记

#### Scenario: 写入不带时区的时间

- **WHEN** 写入的 datetime 没有 tzinfo
- **THEN** 按 UTC 解释后存储

#### Scenario: 写入其他时区的时间

- **WHEN** 写入带非 UTC 偏移的 datetime
- **THEN** 换算到 UTC 存储，取回时表示同一时刻

#### Scenario: 前端展示

- **WHEN** 界面渲染时间
- **THEN** 固定按北京时间格式化，不跟随浏览器所在时区 —— 同一条数据在任何机器上显示同一时刻

