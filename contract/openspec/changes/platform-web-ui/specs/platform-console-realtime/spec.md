## ADDED Requirements

### Requirement: 浏览器订阅实时推送

Platform SHALL 提供独立于 Agent 的 WebSocket 端点，向浏览器单向推送 Agent 上下线与
上传进度，使状态变化无需等待轮询周期即可可见。

#### Scenario: 已登录用户建立连接

- **WHEN** admin 或 recorder 携带有效 JWT 连接 `/ws/console`
- **THEN** 连接被接受
- **AND** 立即收到一次当前在线 Agent 的快照，而不必等下一次状态变化

#### Scenario: token 无效

- **WHEN** 连接携带的 token 签名错误、过期或缺失
- **THEN** Platform 以关闭码 4401 拒绝，且该连接不进入广播池

#### Scenario: 角色无权订阅

- **WHEN** 持有既非 admin 也非 recorder 角色的 token 连接
- **THEN** Platform 以关闭码 4403 拒绝

#### Scenario: 浏览器发来数据帧

- **WHEN** 浏览器向该连接发送任何帧
- **THEN** Platform 忽略其内容，不据此改变任何状态

#### Scenario: 浏览器断开

- **WHEN** 浏览器关闭标签页或连接中断
- **THEN** Platform 将该连接从广播池摘除

#### Scenario: 广播时连接已失效

- **WHEN** 向某个浏览器连接推送失败
- **THEN** Platform 就地摘除该连接
- **AND** 其余浏览器仍收到本次广播

### Requirement: 推送 Agent 上下线

Platform SHALL 在 Agent 连接状态变化时向所有浏览器广播。

#### Scenario: Agent 注册成功

- **WHEN** Agent 完成 WebSocket 注册
- **THEN** 浏览器收到该 agent_id 的 `online: true`

#### Scenario: Agent 断开

- **WHEN** Agent 的 WebSocket 连接断开
- **THEN** 浏览器收到该 agent_id 的 `online: false`

### Requirement: 推送上传进度

Platform SHALL 在上传进度落库后转发给浏览器，计量单位与 Agent 上行帧一致。

#### Scenario: 进度落库

- **WHEN** Agent 上报的进度通过节流判定并写入数据库
- **THEN** 浏览器收到含 episode_id、已完成分片数、总分片数与百分比的帧

#### Scenario: 百分比由服务端计算

- **WHEN** Platform 构造进度帧
- **THEN** `percent` 由分片数算出并随帧下发，前端不各自重算

#### Scenario: 进度被节流跳过

- **WHEN** Agent 上报的进度未通过节流判定
- **THEN** 不落库也不广播

### Requirement: 断线降级不阻塞使用

前端 SHALL 在推送不可用时继续通过轮询工作。

#### Scenario: WebSocket 断开

- **WHEN** 浏览器与 `/ws/console` 的连接中断
- **THEN** 页面继续按轮询周期刷新数据
- **AND** 界面以弱提示告知推送已断开，而不是报错

#### Scenario: 重连

- **WHEN** 连接因网络原因断开
- **THEN** 前端以退避间隔重连，上限 15 秒

#### Scenario: 鉴权失败不重连

- **WHEN** 连接被以 4401 或 4403 关闭
- **THEN** 前端不再重试 —— 同一 token 重试不会有不同结果
