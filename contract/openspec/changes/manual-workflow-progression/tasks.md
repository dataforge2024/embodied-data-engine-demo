# 人工工作流推进任务清单

**原型项目简化原则**（见根目录 `CLAUDE.md`）：只测主流程与核心可靠性，边缘 case 不写测试。
**契约层例外**：状态机改动必须补测试 —— 它是 6 个模块的共同依赖。

顺序有依赖：1 是硬骨头（波及全局），2 依赖 1 的状态，3 依赖 2 能登录，4 独立。

## 1. 契约：新增送标处理状态

- [ ] 1.1 `EpisodeStatus` 新增 `ANNOTATION_PROCESSING`
- [ ] 1.2 状态机：`verification_pending` 出边改为 `{annotation_processing, rejected}`
- [ ] 1.3 状态机：`annotation_processing` 出边为 `{annotation_pending, failed}`
- [ ] 1.4 **测试**：新状态可达、旧的 `verification_pending → annotation_pending` 已非法
- [ ] 1.5 新增 `Dataset` 模型（构建状态、产物位置、纳入清单）
- [ ] 1.6 `openapi/platform.yaml` 新增 `GET /datasets/{dataset_id}`
- [ ] 1.7 重跑 `make contract-gen`，确认 TS 与 JSON Schema 同步

## 2. Platform：工作流推进

- [ ] 2.1 `review.py` 三个端点角色要求收敛为 `ANNOTATOR`（原 `VERIFIER`/`ANNOTATOR`/`REVIEWER`）
- [ ] 2.2 `seed.py` 新增 `annotator` demo 账号 —— 落地后共三个账号：
      `admin`（通配）/ `recorder`（采集+运维）/ `annotator`（质检+标注+审核）
- [ ] 2.3 `submit_verification` 通过时改为进 `annotation_processing`
- [ ] 2.4 新增送标处理完成的推进入口（供 Scheduler 回调）
- [ ] 2.5 送标处理失败时落 `failed` 并记原因
- [ ] 2.6 **测试**：质检通过 → 送标 → 标注 的状态链，以及跳步返回 409

## 3. Scheduler：送标处理落点

- [ ] 3.1 `EpisodePipeline` 新增送标处理入口（本阶段不跑算子，见 design.md 第 2 节）
- [ ] 3.2 处理完成后回调 Platform 推进状态
- [ ] 3.3 处理失败时回调 Platform 落 `failed`

## 4. Tool：登录与身份

- [ ] 4.1 新增登录页，复用 `POST /auth/login`
- [ ] 4.2 登录后 `setAccessToken`，token 存 localStorage
- [ ] 4.3 `currentUser` 改为登录返回的真实 `user_id`
- [ ] 4.4 401 时清 token 并回登录页
- [ ] 4.5 三个页面的提交人改真实 `user_id`

## 5. Tool：标注表单

- [ ] 5.1 分段编辑补 `action_label` 与 `description` 输入
- [ ] 5.2 整条 Episode 的 `notes` 输入
- [ ] 5.3 人工修改过的分段 `source` 置空（标记为人工）
- [ ] 5.4 空分段提交时拦住并给可读提示
- [ ] 5.5 审核页展示标注人提交的分段与备注

## 6. 前端阶段与状态适配

五阶段改六阶段，划分依据见 design.md 第 4a 节。

- [ ] 6.1 `platform/web` 的 `STATUS_LABELS` / `STATUS_COLORS` 补 `annotation_processing`
- [ ] 6.2 `utils/stage.ts` 的 `Stage` 类型由 5 个改 6 个
- [ ] 6.3 `STAGE_ORDER` 改为采集人工作业 / 采集自动解析 / 采集人工质检 /
      标注自动送标 / 标注人工作业 / 完成
- [ ] 6.4 `STAGE_LABELS` 与 `STAGE_HINTS` 按新命名改写
- [ ] 6.5 `STATUS_TO_STAGE`：11 个状态映射到 6 个阶段（`annotation_pending` 与
      `annotation_review` 同归「标注人工作业」，`annotation_processing` 独占「标注自动送标」）
- [ ] 6.6 `countByStage` 的初始计数对象补第 6 格
- [ ] 6.7 **测试**：审核退回时阶段不变（进度条不后退），送标与待标注归不同阶段
- [ ] 6.8 `StageBar` 六格显示（compact 只画点；完整模式六个六字标签需处理挤压）
- [ ] 6.9 `toolLink.ts` 确认 `annotation_processing` 不给人工入口（它在等系统）
- [ ] 6.10 `tool` 侧若有状态映射一并补

## 7. 状态流转记录

记录点收口在 `apply_transition` —— 它是状态写入的唯一入口，见 design.md 第 7 节。

- [ ] 7.1 新增流转历史表：episode_id、源状态、目标状态、发生时间、触发者、原因
- [ ] 7.2 `apply_transition` 追加记录；重放（`changed=False`）与非法迁移都不记
- [ ] 7.3 触发者分两类：人工记 `user_id`，系统记「系统 + 环节名」，不把系统伪装成某个用户
- [ ] 7.4 `episode_lifecycle` 的各方法把触发者传下去（质检/标注/审核/导出传操作人，
      上传回调与算子回调传系统）
- [ ] 7.5 契约新增流转记录模型
- [ ] 7.6 新增 `GET /episodes/{id}/transitions`，按时间正序返回；不存在的 Episode 返 404
- [ ] 7.7 **测试**：正常推进留一条、重放不留、非法迁移不留、触发者归属正确
- [ ] 7.8 控制台展开某条 Episode 的轨迹：时间、源→目标、触发者、原因、停留时长
- [ ] 7.9 人工与系统推进在界面上可区分
- [ ] 7.10 修掉 `stage.ts:78` 的短板：脱轨态借历史标出中断位置，
      不再把所有阶段一律标 `blocked`
- [ ] 7.11 **测试**：失败的 Episode 能定位到死在哪一阶段；质检打回与审核退回可区分

## 8. 导出数据集

- [ ] 8.1 `POST /datasets/build` 权限由 `LAB` 改 `ADMIN`
- [ ] 8.2 校验纳入的 Episode 全部为 `published`，否则 422 并指出不合格的
- [ ] 8.3 新增 `GET /datasets/{id}` 返回构建状态与产物位置
- [ ] 8.4 `build_dataset` Celery task 由 stub 改为落地 `manifest.json`
- [ ] 8.5 manifest 含 episode 清单、人工最终分段、算子产物 object_key、格式、发起人
- [ ] 8.6 构建状态可查（进行中 / 完成 / 失败）

## 9. 端到端验证

- [ ] 9.1 复核现有 e2e：凡走 `verification_pending → annotation_pending` 的都会因中间多一跳而失败
- [ ] 9.2 新增 e2e：五步人工推进走通一遍（质检 → 送标 → 标注 → 审核 → 导出）
- [ ] 9.3 新增 e2e：走完一条后查轨迹，验证每一步都留了记录且顺序正确
- [ ] 9.4 `make check` 全绿
- [ ] 9.5 `make demo` 与 `make demo-rabbit` 都跑通
- [ ] 9.6 浏览器实测：Tool 登录 → 三个工作台各操作一次 → Platform 看到状态推进与轨迹

## 不属于本 change

- [ ] 送标处理接真实算子 —— 前置：明确跑什么、质检信息如何影响预标注（design.md 第 2 节）
- [ ] lerobot / rlds 真实格式转换 —— 单开 change
- [ ] 角色分工恢复（四眼原则）—— 前置：确定实际是否分人
- [ ] Tool 读 URL 参数做深链 —— Platform 侧已带上参数，Tool 侧读取另议
- [ ] 场景 1 遗留的 3 处缺陷（Agent 不回 ack、`TaskCancelFrame` 零处理、
      `upload_progress` 读不到）—— 与本 change 无依赖
- [ ] 流转记录的聚合视图（跨 Episode 看各环节平均耗时、瓶颈环节）—— 本 change 只做单条轨迹
- [ ] 流转记录的归档/清理策略 —— POC 阶段只增不删
- [ ] 任务状态与标注修订的变更历史 —— 本 change 只记 Episode 状态
