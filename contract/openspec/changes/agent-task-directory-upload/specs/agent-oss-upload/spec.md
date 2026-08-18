## ADDED Requirements

### Requirement: 阿里云 OSS 作为对象存储实现

系统 SHALL 提供阿里云 OSS 的对象存储实现，满足既有的对象存储接口约定，使调用方无需改动。

#### Scenario: 实现既有接口

- **WHEN** OSS 实现被注入到依赖对象存储的调用方
- **THEN** 调用方代码无需修改
- **AND** 对象键布局与本地替身保持一致

#### Scenario: 经配置切换后端

- **WHEN** 配置指定使用本地对象存储后端
- **THEN** 系统使用本地目录实现，不要求任何 OSS 凭据
- **AND** 该切换无需改动代码

#### Scenario: 凭据缺失

- **WHEN** 配置指定使用 OSS 后端但缺少必要的凭据或 endpoint
- **THEN** 系统启动时报错并指明缺失的配置项
- **AND** 不以静默降级到本地存储的方式继续运行

#### Scenario: 生产环境拒绝默认凭据

- **WHEN** 运行环境为生产且 OSS 相关配置仍为默认占位值
- **THEN** 系统拒绝启动

### Requirement: Agent 从环境变量读取 OSS 凭据

Agent SHALL 启动时从环境变量读取 OSS 凭据，直接上传到阿里云 OSS，不依赖 Platform 签发临时凭据。

#### Scenario: Agent 启动时读取 OSS 配置

- **WHEN** Agent 容器启动
- **THEN** Agent 读取环境变量 `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` / `OSS_ENDPOINT` / `OSS_BUCKET`
- **AND** 若任一变量缺失或为空，Agent 拒绝启动并报错

#### Scenario: Agent 直接上传到 OSS

- **WHEN** Agent 执行上传
- **THEN** Agent 使用自身配置的 AK/SK 直接调用 OSS API
- **AND** 不调用 Platform 的凭据签发端点

#### Scenario: 凭据不进入代码库

- **WHEN** 部署 Agent
- **THEN** OSS 凭据经 `.env.oss` 文件注入（该文件在 `.gitignore` 中）
- **AND** 代码库中仅包含 `.env.oss.example` 示例文件，含键名但无实际值

### Requirement: 分片上传支持断点续传

系统 SHALL 以分片方式上传，并支持从已完成的分片之后继续，不重传已完成部分。

#### Scenario: 分片上传器接口统一

- **WHEN** 本地实现与 OSS 实现被交替使用
- **THEN** 两者满足同一接口约定，调用方不感知差异

#### Scenario: 每片完成后通知调用方

- **WHEN** 一个分片上传成功
- **THEN** 调用方收到通知并可据此持久化进度

#### Scenario: 续传跳过已完成分片

- **WHEN** 上传因崩溃中断后恢复，本地记录显示分片 1 至 5 已完成
- **THEN** 恢复的上传从分片 6 开始
- **AND** 分片 1 至 5 不重传

#### Scenario: 单片失败后重试

- **WHEN** 某分片上传因网络故障失败
- **THEN** 该分片按配置的次数重试
- **AND** 其他分片的进度不受影响

#### Scenario: 重试耗尽

- **WHEN** 某分片重试次数耗尽仍失败
- **THEN** 上传标记为失败并向调用方报告
- **AND** 已完成的分片状态被保留，供后续续传

#### Scenario: 上传不阻塞事件循环

- **WHEN** Agent 执行上传
- **THEN** Agent 的心跳与 WebSocket 收发不因上传而中断

### Requirement: 上传完成后回调 Platform

Agent SHALL 在上传完成后回调 Platform，并携带从文件实际解析出的元数据。

#### Scenario: 回调携带真实元数据

- **WHEN** 上传完成
- **THEN** 回调中的校验和为文件实际计算所得
- **AND** 回调中的已录制 topic 列表为从文件实际解析所得
- **AND** 回调中的时长为从文件实际解析所得

#### Scenario: Platform 独立校验完整性

- **WHEN** Platform 收到上传完成回调
- **THEN** Platform 独立重算对象的校验和并与回调值比对
- **AND** 不一致时拒绝该回调

#### Scenario: 回调驱动状态流转

- **WHEN** 回调校验通过
- **THEN** Episode 状态由上传中流转为已上传

#### Scenario: 回调重放保持幂等

- **WHEN** 同一回调被重复提交
- **THEN** Episode 状态不重复变更
- **AND** 不重复发布事件
- **AND** 响应表示成功而非冲突

#### Scenario: 上传成功但回调失败

- **WHEN** 对象已成功上传但回调因网络故障失败
- **THEN** Agent 持久化该状态
- **AND** Agent 重启后补发该回调
- **AND** 不重传已上传的对象

### Requirement: 上传凭据不进入版本库

系统 SHALL 确保对象存储凭据不被提交到版本库。

#### Scenario: 凭据经环境文件注入

- **WHEN** 部署 Agent 容器
- **THEN** 凭据经被版本库忽略的环境文件注入
- **AND** 版本库中仅包含只有键名与说明的示例文件

#### Scenario: 编排配置不含凭据值

- **WHEN** 容器编排配置被提交
- **THEN** 该配置仅引用环境文件，不含任何凭据的实际值
