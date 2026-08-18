## ADDED Requirements

### Requirement: 上传进度经 WebSocket 实时回传

Agent SHALL 在每个分片完成后经 WebSocket 上报进度，不做节流，以保证观测方的实时性。

#### Scenario: 每片完成即上报

- **WHEN** 一个分片上传成功
- **THEN** Agent 发送上传进度帧
- **AND** 帧中含 Episode 标识、已完成分片数与总分片数

#### Scenario: WebSocket 断连期间的上传

- **WHEN** 上传进行中而 WebSocket 处于断连状态
- **THEN** 上传继续进行，不因无法上报而中断
- **AND** 进度帧被丢弃而非堆积

#### Scenario: 重连后恢复上报

- **WHEN** WebSocket 重连成功且上传仍在进行
- **THEN** 后续分片的进度正常上报

### Requirement: Platform 节流持久化上传进度

Platform SHALL 持久化上传进度以支持观测方重新加载后恢复显示，并以节流写入避免写放大。

#### Scenario: 进度可被持久化查询

- **WHEN** 观测方在上传进行中查询 Episode
- **THEN** 响应包含当前上传进度

#### Scenario: 观测方重新加载后进度不丢失

- **WHEN** 观测方在上传进行中重新加载页面
- **THEN** 进度从持久化的值恢复，而非归零显示

#### Scenario: 进度变化达到阈值时写入

- **WHEN** 收到的进度相比上次写入的值增加达到阈值比例
- **THEN** 持久化该进度

#### Scenario: 距上次写入超过时间阈值时写入

- **WHEN** 收到进度帧且距上次写入已超过时间阈值
- **THEN** 持久化该进度

#### Scenario: 高频进度帧不产生等量写入

- **WHEN** 一次上传产生约两千个进度帧
- **THEN** 持久化写入次数远少于帧数
- **AND** 实时性由 WebSocket 保证，不因节流受损

#### Scenario: 重连后首帧触发写入

- **WHEN** WebSocket 断连重连后收到第一个进度帧
- **THEN** 该帧触发一次持久化写入
- **AND** 节流状态随连接结束而丢弃，不跨连接保留

#### Scenario: 进度以比例表示

- **WHEN** 进度被持久化
- **THEN** 其值为 0 至 1 之间的比例
- **AND** 该表示不随分片大小配置变化而改变含义

### Requirement: 心跳携带真实的队列长度与磁盘水位

Agent SHALL 在心跳中上报真实的待上传队列长度与剩余磁盘空间，不使用模拟值。

#### Scenario: 上报真实待上传数

- **WHEN** 各任务目录中共有 4 个已通过校验但尚未开始上传的文件
- **THEN** 心跳中的待上传队列长度为 4

#### Scenario: 上报真实磁盘剩余空间

- **WHEN** Agent 发送心跳
- **THEN** 剩余磁盘空间取自监听根目录所在文件系统的实际可用空间

#### Scenario: 上报正在录制的 Episode

- **WHEN** Agent 当前有文件正在上传
- **THEN** 心跳如实反映该状态

#### Scenario: 心跳不因上传繁忙而中断

- **WHEN** Agent 正在上传大文件
- **THEN** 心跳仍按配置的间隔发送
- **AND** Platform 不将该 Agent 判定为离线

### Requirement: Platform 记录 Agent 在线状态与心跳

Platform SHALL 依据心跳维护 Agent 的在线状态，供运维观测。

#### Scenario: 心跳更新在线状态

- **WHEN** Platform 收到心跳
- **THEN** 该 Agent 被标记为在线
- **AND** 最近一次心跳内容被记录

#### Scenario: 心跳超时判定离线

- **WHEN** 某 Agent 在配置的超时时间内未发送心跳
- **THEN** 该 Agent 被判定为离线

#### Scenario: 运维视图可见磁盘水位

- **WHEN** 查询 Agent 节点列表
- **THEN** 响应包含各 Agent 的在线状态、剩余磁盘空间与待上传队列长度
