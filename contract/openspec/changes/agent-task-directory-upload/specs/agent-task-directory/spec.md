## ADDED Requirements

### Requirement: 任务下发时创建任务目录

Agent 收到任务推送后，SHALL 在监听根目录下创建对应的任务目录，目录名为 `<slug(任务名)>__<task_id>`。

#### Scenario: 收到任务推送

- **WHEN** Agent 经 WebSocket 收到 `TaskPushFrame`，其 `payload.task_name` 为 `厨房抓取`、`payload.task_id` 为 `t-a3f9c1`
- **THEN** Agent 在监听根目录下创建目录 `厨房抓取__t-a3f9c1/`
- **AND** 目录内写入 `.task.json`
- **AND** Agent 回复 `AckFrame`，其 `message_id` 与推送帧一致

#### Scenario: 目录已存在

- **WHEN** Agent 收到某任务的推送，而该任务目录已存在
- **THEN** Agent 不报错、不覆盖目录内已有的 MCAP 文件
- **AND** 仅在 `.task.json` 内容与推送内容不一致时更新该文件

#### Scenario: 创建目录失败

- **WHEN** 创建目录因权限不足或磁盘写满而失败
- **THEN** Agent 记录错误日志，包含 `task_id` 与失败原因
- **AND** Agent 不回复 `AckFrame`
- **AND** Agent 的 WebSocket 连接保持，不因此断开

### Requirement: 任务名转换为文件系统安全的目录名

系统 SHALL 将任务名转换为可安全用作目录名的 slug，同时保留中文字符以维持可读性。

#### Scenario: 任务名含路径分隔符与空白

- **WHEN** 任务名为 `厨房抓取/放置 (v2)`
- **THEN** slug 为 `厨房抓取-放置-v2`

#### Scenario: 任务名含文件系统非法字符

- **WHEN** 任务名包含 `( ) [ ] { } < > : " | ? * \` 或控制字符中的任意字符
- **THEN** 这些字符被移除
- **AND** 结果中不出现连续的 `-`

#### Scenario: 任务名以点号开头

- **WHEN** 任务名为 `.隐藏任务`
- **THEN** slug 不以 `.` 开头，避免生成隐藏目录

#### Scenario: 任务名超长

- **WHEN** 任务名长度超过 60 个字符
- **THEN** slug 被截断至 60 个字符（按字符计，非字节）

#### Scenario: 任务名转换后为空

- **WHEN** 任务名仅由会被移除的字符组成，例如 `***`
- **THEN** slug 回退为 `task`

#### Scenario: 任务名本身含双下划线

- **WHEN** 任务名为 `抓取__放置`、`task_id` 为 `t-a3f9c1`
- **THEN** 目录名为 `抓取__放置__t-a3f9c1`
- **AND** 从目录名解析 `task_id` 时，从右侧首个 `__` 处切分，得到 `t-a3f9c1`

### Requirement: 任务目录内的任务元数据文件

任务目录 SHALL 包含 `.task.json`，载明任务快照与采集要求，供采集人员无需登录网页即可查阅。

#### Scenario: 元数据文件内容

- **WHEN** Agent 为任务创建目录
- **THEN** `.task.json` 包含 `task_id`、`name`（未经 slug 的原始任务名）、`assigned_at`
- **AND** 包含完整的 `requirement`，含 `robot_model`、`scene`、`required_topics`、`min_duration_ms`、`max_duration_ms`、`target_episode_count`
- **AND** 包含 `progress`，其中的计数字段命名为 `uploaded` 而非 `published`，如实反映 Agent 只统计自身上传数

#### Scenario: 元数据文件是 task_id 的权威来源

- **WHEN** 目录名解析出的 `task_id` 与 `.task.json` 中的 `task_id` 不一致
- **THEN** 系统采用 `.task.json` 中的值

#### Scenario: 元数据文件缺失或损坏

- **WHEN** 任务目录存在但 `.task.json` 缺失，或内容无法解析为合法 JSON
- **THEN** 系统从目录名回退解析 `task_id`
- **AND** 记录警告日志

### Requirement: Agent 启动与重连后重建任务目录

Agent SHALL 在进程启动时与 WebSocket 重连成功后拉取已分派任务，并幂等地确保对应目录存在。

#### Scenario: 容器重启后 volume 为空

- **WHEN** Agent 容器重建，监听根目录为空，而 Platform 侧存在 3 个分派给该 Agent 的任务
- **THEN** Agent 启动后创建全部 3 个任务目录
- **AND** 每个目录内写入 `.task.json`

#### Scenario: WebSocket 重连后补齐断连期间的分派

- **WHEN** Agent 断连期间 Admin 分派了一个新任务，随后 Agent 重连
- **THEN** Agent 拉取到该任务并创建目录
- **AND** 断连期间的推送失败不导致任务丢失

#### Scenario: 拉取到的任务目录已存在

- **WHEN** Agent 拉取到的任务其目录已存在且含未上传的 MCAP 文件
- **THEN** Agent 保留这些文件不动
- **AND** 不重复创建目录

#### Scenario: 拉取失败

- **WHEN** 拉取已分派任务的请求因网络故障或 Platform 不可用而失败
- **THEN** Agent 记录错误但继续启动
- **AND** Agent 处理监听根目录下已存在的任务目录中的文件

### Requirement: Platform 提供已分派任务查询端点

Platform SHALL 提供端点供 Agent 查询分派给自己的任务。

#### Scenario: 查询已分派任务

- **WHEN** Agent 以有效的 Agent token 调用 `GET /agents/me/tasks`
- **THEN** 响应包含该 Agent 所有 `assigned` 状态任务的列表
- **AND** 每项包含 `task_id`、`task_name`、`requirement`

#### Scenario: 无效凭据

- **WHEN** 调用方未提供 Agent token 或提供无效 token
- **THEN** 响应状态码为 401

#### Scenario: Agent 身份由凭据决定

- **WHEN** Agent 调用该端点
- **THEN** 返回的任务范围由 token 解析出的 Agent 身份决定
- **AND** 端点不接受路径或查询参数指定其他 Agent 的身份

#### Scenario: 该 Agent 无已分派任务

- **WHEN** 没有任务分派给该 Agent
- **THEN** 响应成功，任务列表为空

### Requirement: 任务达成目标条数后标记目录

Agent SHALL 在上传条数达到目标后标记目录，但不阻止继续放入文件。

#### Scenario: 达到目标条数

- **WHEN** 某任务成功上传的 Episode 数达到 `requirement.target_episode_count`
- **THEN** 目录名追加 `__已完成` 后缀
- **AND** `.task.json` 的 `progress.uploaded` 反映实际上传数

#### Scenario: 达成后继续放入文件

- **WHEN** 已标记 `__已完成` 的目录中被放入新的 MCAP 文件
- **THEN** 该文件仍被正常处理并上传
- **AND** 不因目录已标记而拒绝

### Requirement: 任务取消时的目录处置

Agent 收到任务取消通知后，SHALL 让进行中的上传自然完成，并标记目录。

#### Scenario: 取消时有正在上传的文件

- **WHEN** Agent 收到 `TaskCancelFrame`，而该任务有文件正处于上传中
- **THEN** 该文件的上传继续直至完成，不中断
- **AND** 避免在对象存储中留下未完成的分片

#### Scenario: 取消时有待上传的文件

- **WHEN** Agent 收到 `TaskCancelFrame`，任务目录中存在尚未开始上传的 MCAP 文件
- **THEN** 这些文件被移入 `.cancelled/` 子目录
- **AND** 不为它们创建 Episode

#### Scenario: 取消后标记目录

- **WHEN** Agent 完成取消处理
- **THEN** 目录名追加 `__已取消` 后缀
