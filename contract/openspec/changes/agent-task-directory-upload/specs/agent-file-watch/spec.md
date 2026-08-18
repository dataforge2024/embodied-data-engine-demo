## ADDED Requirements

### Requirement: 监听任务目录中的 MCAP 文件

Agent SHALL 监听各任务目录顶层的 `*.mcap` 文件，并忽略点号开头的路径。

#### Scenario: 文件被放入任务目录

- **WHEN** 采集人员将 `ep_001.mcap` 拷入任务目录
- **THEN** Agent 检测到该文件并进入待检测状态

#### Scenario: 文件被移入任务目录

- **WHEN** 采集人员用移动而非拷贝的方式把 MCAP 文件放入任务目录
- **THEN** Agent 同样检测到该文件

#### Scenario: 忽略内部子目录

- **WHEN** 文件出现在 `.uploading/`、`.done/`、`.failed/`、`.rejected/` 或 `.cancelled/` 中
- **THEN** Agent 不将其视为新文件处理

#### Scenario: 忽略元数据文件

- **WHEN** `.task.json` 被创建或修改
- **THEN** Agent 不将其视为待上传文件

#### Scenario: 非 MCAP 后缀的文件

- **WHEN** 任务目录中出现 `.DS_Store`、`.tmp` 后缀或 `~$` 前缀的文件
- **THEN** Agent 静默忽略，不产生错误日志、不移动文件

#### Scenario: 其他非预期后缀的文件

- **WHEN** 任务目录中出现既非 `*.mcap` 也不在静默忽略名单中的文件，例如 `readme.txt`
- **THEN** Agent 将其移入 `.rejected/` 并写入说明文件
- **AND** 说明文件指出该文件后缀不受支持

### Requirement: 检测文件写入完成

Agent SHALL 在文件写入完成后才开始处理，避免读取到残缺文件。

#### Scenario: 大文件正在写入

- **WHEN** 一个 500MB 的 MCAP 文件正在被写入，`watchdog` 已触发创建事件
- **THEN** Agent 不立即读取该文件
- **AND** Agent 按配置的间隔采样文件大小

#### Scenario: 文件大小趋于稳定

- **WHEN** 文件大小连续 3 次采样保持不变
- **THEN** Agent 视该文件写入完成并进入解析阶段

#### Scenario: 文件大小仍在变化

- **WHEN** 采样期间文件大小发生变化
- **THEN** 稳定计数重置
- **AND** Agent 继续采样

#### Scenario: 出现完成标记文件

- **WHEN** 任务目录中出现 `ep_001.mcap.done`
- **THEN** Agent 立即处理 `ep_001.mcap`，跳过大小稳定采样
- **AND** 处理完成后移除该标记文件

#### Scenario: 采样参数可配置

- **WHEN** 部署环境为慢速网络存储
- **THEN** 采样间隔与稳定次数可经配置调整，无需改动代码

### Requirement: 识别 MCAP 文件格式并解析元数据

Agent SHALL 按文件头识别 MCAP 格式，并从两种支持的格式中解析出统一的元数据。

#### Scenario: 标准 MCAP 文件

- **WHEN** 文件前 8 个字节为标准 MCAP 的魔数 `\x89MCAP0\r\n`
- **THEN** Agent 使用标准 MCAP 解析路径
- **AND** 解析出 topic 列表与时长

#### Scenario: 本项目 JSON Lines 格式

- **WHEN** 文件以 `{` 开头
- **THEN** Agent 使用 JSON Lines 解析路径
- **AND** 解析出 topic 列表与时长

#### Scenario: 两种格式产出一致的元数据结构

- **WHEN** 任一格式解析成功
- **THEN** 产出统一的元数据结构，含 topic 列表与时长
- **AND** 下游的预检与回调逻辑不感知格式差异

#### Scenario: 无法识别的格式

- **WHEN** 文件头既非 MCAP 魔数也非 `{`
- **THEN** Agent 将文件移入 `.rejected/` 并写入说明文件
- **AND** 说明文件指出格式无法识别
- **AND** 不为该文件创建 Episode

#### Scenario: 格式正确但内容残缺

- **WHEN** 文件头可识别，但内容结构不完整（例如写入过程被中断）
- **THEN** 解析失败，文件移入 `.rejected/` 并写入说明文件
- **AND** 该行为使写入完成误判的后果限于需人工重放，而非上传坏数据

### Requirement: 上传前校验采集要求

Agent SHALL 在上传前比对解析出的 topic 与任务要求，不达标则拒绝，以避免无效的大文件传输。

#### Scenario: topic 满足要求

- **WHEN** 解析出的 topic 列表包含 `requirement.required_topics` 中的全部条目
- **THEN** 文件进入上传阶段

#### Scenario: 缺少必需 topic

- **WHEN** `requirement.required_topics` 含 `/camera/front/image_raw`，而文件中不存在该 topic
- **THEN** Agent 将文件移入 `.rejected/`
- **AND** 写入说明文件，列出缺失的 topic
- **AND** 不为该文件创建 Episode
- **AND** 不发生任何上传流量

#### Scenario: 含要求之外的额外 topic

- **WHEN** 文件包含 `required_topics` 之外的其他 topic
- **THEN** 不影响校验通过

#### Scenario: 不校验时长

- **WHEN** 文件时长短于 `requirement.min_duration_ms` 或长于 `max_duration_ms`
- **THEN** Agent 不拒绝该文件
- **AND** 时长判断留给核验环节的人工裁量

### Requirement: 文件在处理各阶段间流转

Agent SHALL 通过子目录标记文件所处阶段，且默认不删除原文件。

#### Scenario: 开始上传

- **WHEN** 文件通过校验并开始上传
- **THEN** 文件被移入 `.uploading/`

#### Scenario: 上传并回调成功

- **WHEN** 全部分片上传成功且上传完成回调成功
- **THEN** 文件被移入 `.done/`

#### Scenario: 上传失败

- **WHEN** 某分片重试次数耗尽仍失败
- **THEN** 文件被移入 `.failed/`
- **AND** 同目录写入说明文件，含失败阶段、错误类型与最后一次错误信息

#### Scenario: 默认保留已上传文件

- **WHEN** 文件成功上传
- **THEN** 本地副本默认保留在 `.done/`，供核对云端一致性

#### Scenario: 配置为上传后删除

- **WHEN** 配置指定不保留已上传文件
- **THEN** 成功上传后删除本地副本，不移入 `.done/`

#### Scenario: 断电后恢复上传中的文件

- **WHEN** Agent 在上传过程中崩溃或断电，重启后 `.uploading/` 中存在文件
- **THEN** Agent 依据本地持久化的分片状态续传
- **AND** 已完成的分片不重传
