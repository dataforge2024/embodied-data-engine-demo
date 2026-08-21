# Episode 生命周期

Episode 是一次采集的最小单元（一个 MCAP 文件）。状态机的权威定义在
[`src/rdh_contract/state_machine.py`](../src/rdh_contract/state_machine.py)，本文档解释每个状态的
语义与负责方。**代码是事实来源，文档与代码不一致时以代码为准**，`tests/test_state_machine.py`
保证代码自洽。

## 三个容易混淆的设计点

[架构文档](architecture.md)给出状态机全貌与展示层的六阶段映射；本文补的是各状态的语义、
负责方与幂等要求。以下三点最容易读错：

**两个失败态是分开的。** 主链路只描述顺利路径，失败要有落点：

| 状态 | 何时用 | 为什么不能合并 |
|---|---|---|
| `rejected` | 人工核验判定数据不可用 | 否则打回的数据会卡在 `verification_pending` |
| `failed` | 上传中断、MCAP 解析失败、算子报错 | 系统故障要与「人工判定不可用」区分开 |

**标注审核「退回」不是新状态**，而是回到 `annotation_pending` 重做，`Annotation.revision`
计数 +1。退回重做与核验打回语义不同，不要混用 `rejected`。

**`annotation_processing` 是独立中间态。** 质检通过后先过一个异步送标环节，不直接进
`annotation_pending`。它与 `processing` 分开而不复用，因为两者回调语义不同（一个「解析完
等人看」，一个「送标完等人标」）。理由见
[归档的 design.md](../openspec/changes/archive/2026-08-21-manual-workflow-progression/design.md)
第 1 节。本阶段该环节不跑算子（同文档第 2 节）。

## 状态流转图

```
                    ┌──────────┐
                    │ recording│ ← 初始态（Agent 开始录制）
                    └────┬─────┘
                         │ 录制结束
                    ┌────▼─────┐
                    │ uploading│──────────┐
                    └────┬─────┘          │
                         │ 交互③ 上传回调  │
                    ┌────▼─────┐          │
                    │ uploaded │──────────┤
                    └────┬─────┘          │
                         │ 交互⑤⑥ 事件消费 │ 任何环节异常
                   ┌─────▼──────┐         │
                   │ processing │─────────┤
                   └─────┬──────┘         │
                         │ 交互⑧ 算子结果  │
            ┌────────────▼───────────┐    │
            │ verification_pending   │    │
            └────┬──────────────┬────┘    │
       核验通过  │              │ 核验打回 │
    ┌────────────▼──────────┐   │         │
    │ annotation_processing │───┼─────────┤ 送标处理失败
    └────────────┬──────────┘   │         │
       送标完成   │              │         │
     ┌───────────▼────────┐     │         │
     │ annotation_pending │     │         │
     └────┬───────────────┘     │         │
          │ 提交标注       ▲     │         │
     ┌────▼──────────────┐│退回 │         │
     │ annotation_review ├┘重做 │         │
     └────┬─────────┬────┘      │         │
   审核通过│         │ 判定不可用 │         │
     ┌────▼────┐   ┌▼──────────▼┐   ┌────▼───┐
     │published│   │  rejected  │   │ failed │
     └─────────┘   └────────────┘   └────────┘
       ↑ 终态          ↑ 终态           ↑ 终态
```

## 各状态明细

| 状态 | 语义 | 进入条件 | 退出去向 | 谁触发 |
|---|---|---|---|---|
| `recording` | Agent 正在录制 | Agent 上报开始录制（初始态） | `uploading` / `failed` | Agent（WS 交互①） |
| `uploading` | 分片上传中 | 录制结束 | `uploaded` / `failed` | Agent（交互②） |
| `uploaded` | MCAP 已在 MinIO，待处理 | 上传回调 + checksum 校验通过 | `processing` / `failed` | Platform（交互③） |
| `processing` | 解析 + 算子流水线执行中 | Scheduler 消费 `episode.uploaded` | `verification_pending` / `failed` | Scheduler（交互⑥⑦） |
| `verification_pending` | 待人工核验 | 流水线全部算子成功（`pipeline_complete=true`） | `annotation_processing` / `rejected` | Scheduler 回调（交互⑧） |
| `annotation_processing` | 送标处理中（异步，本阶段不跑算子） | 核验通过 | `annotation_pending` / `failed` | Annotator 触发 → Scheduler 回调 |
| `annotation_pending` | 待人工标注 | 送标处理完成 | `annotation_review` / `rejected` | Scheduler 回调（送标完成） |
| `annotation_review` | 待标注审核 | 标注提交 | `published` / `annotation_pending` / `rejected` | Annotator（交互④） |
| `published` | 已发布，可并入训练集 | 审核通过 | 终态 | Reviewer（交互④） |
| `rejected` | 人工判定不可用 | 核验打回或审核判定不可用 | 终态 | Verifier / Reviewer |
| `failed` | 系统异常 | 录制中断 / 上传失败 / 解析失败 / 算子报错 | 终态，需人工介入 | Agent 或 Scheduler |

## 强制约束

**唯一入口**：Platform 侧所有状态变更必须经 `app/services/episode_lifecycle.py`，
该服务调用 `assert_transition(source, target)` 做守卫。Repository 层不得暴露裸的 status 赋值。

违反此约束的典型后果：核验打回的 Episode 被后台任务重新推进流水线，人工判定被系统覆盖。

**409 而非 500**：`InvalidTransitionError` 应转成 HTTP 409 Conflict，且错误信息不泄露内部状态细节。

**Agent 上报不是权威**：`EpisodeStatusFrame`（WS 上行）只是 Agent 的观察，Platform 仍需过守卫。
Agent 可能因断电恢复而重放旧状态。

**事件发布在状态落库之后**：先提交事务再发 RabbitMQ。反过来会导致 Scheduler 消费到
Platform 还查不到的 Episode。

## 幂等要求

RabbitMQ 至少一次投递，同一事件可能重复到达。消费方按 `event_id` 去重。

状态迁移天然幂等：`uploaded → processing` 重放时第二次会因 `processing → processing` 非法而被守卫
拒绝，Platform 应把这种情况识别为「已处理」而非报错。
