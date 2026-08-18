# robotdatahub-testing

横向质量保障。pytest + Locust（Playwright 待前端实装后接入）。

## 我依赖 contract 的什么

| 契约项 | 用途 |
|---|---|
| `events.EVENT_REGISTRY` | 校验事件的发布方与消费方成对存在 |
| `enums` / `state_machine` | 断言状态流转符合契约 |
| `schemas` | 构造测试数据 |

Testing 是**唯一允许跨模块**的地方 —— 它的职责就是验证模块间的配合。业务模块之间仍然
互不依赖，这一点由 `contract_checks` 强制校验。

## 三层测试

### `contract_checks/` — 契约一致性（不需要起服务）

最有价值的部分：单模块自测发现不了的错位，在这里暴露。

| 检查 | 拦住什么 |
|---|---|
| 依赖铁律 | `scheduler/` 里出现 `from app ...` |
| 契约版本对齐 | 某模块钉了 0.1.0 但 contract 已经是 0.2.0 |
| OpenAPI vs 实现 | 规范声明了端点但 Platform 没实现 |
| 事件接线 | 事件发出去无人消费 |
| 状态机收口 | 绕过 `episode_lifecycle` 直接改 status |
| 事件出口收口 | 绕过 `event_publisher` 直接写队列目录 |
| 硬编码密钥 | 源码里出现真实 token |

用**静态解析**而非 import 各模块 —— Testing 不该依赖它们的运行环境。

### `e2e/` — 端到端流程

in-process 驱动（不起 uvicorn），因此能在 CI 跑：

| 用例 | 覆盖 |
|---|---|
| `test_full_pipeline_reaches_published` | 完整主链路 8 跳到 published |
| `test_verification_reject_terminates_episode` | 核验打回 → rejected 终态 + 事件 |
| `test_illegal_transition_is_rejected` | 终态不可复活 |
| `test_upload_callback_replay_is_idempotent` | 重放不重复发事件 |
| `test_agent_recovery_resumes_partial_upload` | 断电恢复只补缺口分片 |
| `test_scheduler_sends_bad_event_to_dlq` | 不合契约的消息进死信不阻塞队列 |

### `load/` — 压测

Locust 场景。本阶段只有骨架，需要真实 Platform 实例才有意义。

## 运行

```bash
make conformance     # 契约一致性（快，无需起服务）
make e2e             # 端到端
make demo            # 看得见的完整演示
```

## 覆盖率说明

本阶段 `contract/` 守 80% 覆盖率（`make contract-cov`）。业务模块的覆盖率门槛等各自
补齐单测后再启用 —— 现在的质量保障来自 `contract_checks` 的结构性校验 + `e2e` 的链路验证，
这两层拦得住「模块间配合出错」，而那是本阶段最大的风险。
