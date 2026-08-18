# Agent 目录上传实现任务清单

**原型项目简化原则**（见根目录 `CLAUDE.md`）：只测主流程与核心可靠性，边缘 case 不写测试。

## 1. 前置修复

- [ ] 1.1 统一运行时目录：`scripts/demo.py` 与 `platform/app/core/config.py` 共用常量
- [ ] 1.2 补幂等用户 seed：各角色一个 demo 用户
- [ ] 1.3 验证登录可用：`POST /auth/login` → token，`GET /auth/me` → 用户信息

## 2. Contract 扩展

- [ ] 2.1 验证 `AgentTaskPush` 已含 `task_name` / `requirement`（无需新增）
- [ ] 2.2 验证 `TaskRequirement` 已含 `required_topics` / `target_episode_count`
- [ ] 2.3 验证 `TaskCancelFrame` 已存在（无需新增）
- [ ] 2.4 验证 `UploadProgressFrame` 字段足够（可能需补 `file_name`）
- [ ] 2.5 生成 TS 类型并验证同步

## 3. 目录命名与元数据

- [ ] 3.1 实现 slugify：特殊字符折叠为 `-`，60 字符截断
- [ ] 3.2 实现目录名 `<slug>__<task_id>` 构造与解析
- [ ] 3.3 实现 `.task.json` 读写：`task_id` / `name` / `requirement` / `uploaded_count`
- [ ] 3.4 优先级：`.task.json` > 目录名

## 4. 任务下发与目录创建

- [ ] 4.1 Platform 新增 `GET /agents/me/tasks`，返回该 Agent 的 `assigned` 任务
- [ ] 4.2 Agent 处理 `TaskPushFrame`：创建目录 → 写元数据 → 回 ack
- [ ] 4.3 Agent 启动时拉取已分派任务并重建目录
- [ ] 4.4 处理 `TaskCancelFrame`：未开始的移 `.cancelled/`

## 5. MCAP 解析

- [ ] 5.1 新增 `mcap` 依赖
- [ ] 5.2 实现格式嗅探：前 8 字节区分标准 MCAP vs JSON Lines
- [ ] 5.3 实现标准 MCAP 解析（topic 列表 + 时长）
- [ ] 5.4 实现 JSON Lines 解析（复用现有 reader）
- [ ] 5.5 统一元数据结构
- [ ] 5.6 **测试**：两种格式产出一致，残缺文件报错

## 6. 目录监听

- [ ] 6.1 新增 `watchdog` 依赖
- [ ] 6.2 监听 `*.mcap`（创建 + 移入），忽略 `.` 开头
- [ ] 6.3 大小稳定检测：1s 采样，连续 3 次不变 → 写完
- [ ] 6.4 检测完成后排队处理
- [ ] 6.5 **测试**：临时目录模拟写入完成

## 7. 采集要求预检

- [ ] 7.1 topic 比对：解析结果含全部 `required_topics`
- [ ] 7.2 不达标 → `.rejected/` + 写说明 + 不上传
- [ ] 7.3 **测试**：通过 / 缺 topic

## 8. OSS 上传

- [ ] 8.1 新增 `oss2` 依赖
- [ ] 8.2 定义 `ChunkUploader` Protocol（签名沿用现有）
- [ ] 8.3 实现 `OSSChunkUploader`：分片上传 + 断点续传 + 回调
- [ ] 8.4 实现 `OSSObjectStore` 满足 `ObjectStore` Protocol
- [ ] 8.5 Agent 启动时从环境变量读取 OSS 配置（AK/SK/endpoint/bucket）
- [ ] 8.6 OSS 凭据缺失时启动报错
- [ ] 8.7 上传经 `asyncio.to_thread` 不阻塞事件循环
- [ ] 8.8 **测试**：本地后端 + mock OSS 客户端

## 9. 文件流转与恢复

- [ ] 9.1 实现流转状态机：`pending` → `uploading` → `uploaded` → `callback_sent`
- [ ] 9.2 上传成功 → `.done/`
- [ ] 9.3 上传失败保留原地等恢复
- [ ] 9.4 回调成功 → `.done/`（配置可选删除）
- [ ] 9.5 Agent 启动扫描残局：有分片 DB 记录但未完成的 → 续传队列
- [ ] 9.6 回调失败的补发
- [ ] 9.7 **测试**：断电模拟（写一半分片 + 重启）

## 10. 上传进度 WS 推送

- [ ] 10.1 每片完成回调更新进度
- [ ] 10.2 构造 `UploadProgressFrame` 并发送
- [ ] 10.3 节流：同文件推送间隔 ≥ 1s
- [ ] 10.4 连接断开期间的进度丢弃（不堆积）
- [ ] 10.5 **测试**：mock WS 连接验证推送

## 11. Platform 进度落库与展示

- [ ] 11.1 Platform 收到进度帧时节流写入（≥5% 或 ≥2s）
- [ ] 11.2 `GET /tasks/{id}` 返回任务进度（uploaded / target）
- [ ] 11.3 `GET /episodes/{id}` 返回上传进度
- [ ] 11.4 达成 `target_episode_count` 后标记已完成

## 12. 端到端验证

- [ ] 12.1 扩展 demo：分派任务 → 模拟文件落地 → 监听触发 → 上传 → 进度推送
- [ ] 12.2 验证 OSS 后端可切换且不影响逻辑
- [ ] 12.3 验证断电恢复场景

---

**已砍内容**（记录在 `specs/` 但不写测试）：

- slugify 边缘 case（中文、超长、双下划线）
- 静默忽略名单（`.DS_Store` / `.tmp`）
- `.done` 标记文件
- 采样参数可配置
- 生产凭据校验扩展
- 凭据作用域与 TTL
- 分片重试细节
- 目录重命名后缀（`__已完成` / `__已取消`）
- 所有「幂等」「已存在」「权限不足」的防御性场景

这些行为在实现时仍按 spec 处理，但不逐条写单测验证。
