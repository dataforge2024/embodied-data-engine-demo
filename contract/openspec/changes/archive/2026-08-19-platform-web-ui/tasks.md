# Platform Web UI 实现任务清单

**原型项目简化原则**（见根目录 `CLAUDE.md`）：只测主流程与核心可靠性，边缘 case 不写测试。
契约层例外，保持 80% 覆盖率。

全部条目已完成。勾选状态反映最终交付，不是实现顺序 —— 第 6、7 节是实现中被真实链路
暴露出来后追加的。

## 1. 契约扩展

- [x] 1.1 新增 `ConsoleAgentStatusFrame`（`console.agent_status`）
- [x] 1.2 新增 `ConsoleUploadProgressFrame`（`console.upload_progress`），计量单位用分片数
- [x] 1.3 新增 `CONSOLE_ADAPTER` 判别联合，`ws/__init__.py` 导出
- [x] 1.4 `Episode` 增 `recorded_by`（采集员归属）
- [x] 1.5 重新生成 `types/contract.ts`，验证 TS 与 Python 同步
- [x] 1.6 补契约测试：帧往返、percent 越界、`total_parts=0` 除零

## 2. Platform 后端

- [x] 2.1 `ConnectionManager` 增浏览器连接池，与 Agent 连接池分开
- [x] 2.2 `broadcast_console` 广播，发送失败就地摘除死连接
- [x] 2.3 `/ws/console` 端点：query 参数鉴权，4401/4403 拒绝码
- [x] 2.4 连上先推一次在线快照，避免页面干等下一次状态变化
- [x] 2.5 Agent 注册/断开时广播上下线
- [x] 2.6 上传进度落库后转发给浏览器，percent 在服务端算好
- [x] 2.7 新增 `GET /users`（`recorded_by` → `display_name` 反查）
- [x] 2.8 `GET /episodes` 支持 `task_id` 过滤（父子关系的数据基础）
- [x] 2.9 `POST /episodes` 用 JWT 的 `sub` 填 `recorded_by`，不采信 Agent 上报

## 3. 前端基础设施

- [x] 3.1 `api/console-socket.ts`：退避重连 1s→15s，4401/4403 不重连
- [x] 3.2 `hooks/useConsoleStream.ts`：帧折叠成页面可用状态
- [x] 3.3 `hooks/useCurrentUser.ts`：角色判定，admin 通配（与后端一致）
- [x] 3.4 `utils/datetime.ts`：固定 `Asia/Shanghai`，不跟随浏览器时区
- [x] 3.5 `utils/stage.ts`：10 状态 → 5 阶段的分组表 + 脱轨态
- [x] 3.6 Vite 代理放行 WS Upgrade（`ws: true`）

## 4. 前端页面

- [x] 4.1 工作区按角色收敛，无权账号给明确提示而非空白页
- [x] 4.2 任务列表页：新建任务（含选 Agent 下发）、分派/改派
- [x] 4.3 任务详情页：任务要求、阶段汇总、子任务列表、面包屑返回
- [x] 4.4 采集记录页：跨任务历史，按任务与子状态筛选
- [x] 4.5 运维监控页收敛为纯查看 —— 任务创建移到任务管理，此页只留触发回传
- [x] 4.6 `components/EpisodeTable`：两个页面共用一份列定义
- [x] 4.7 `components/StageBar`：阶段进度条 + 脱轨态单独画法
- [x] 4.8 任务进度按**已采集**算而非已发布（上传完就该看到动静）
- [x] 4.9 表单默认 topics 对齐录制器实际产出

## 5. 缺陷修复：跨模块状态机断链

- [x] 5.1 定位 `uploaded → processing` 无人执行（只有 demo 与 e2e 手动补跳）
- [x] 5.2 Platform 在发出 `episode.uploaded` 后自己推进到 `processing`（方案 A）
- [x] 5.3 `mark_uploaded` 重放判断改为「不是 `uploading`」，兼容 Agent 补发回调
- [x] 5.4 删掉 demo 与 e2e 里的手动补跳 —— 它们掩盖了生产缺陷
- [x] 5.5 补 `tests/test_uploaded_enters_processing.py`（5 例，含当初 409 的那条路径）

## 6. 缺陷修复：Agent

- [x] 6.1 `watcher.py` 路径过滤改为只看相对 `watch_root` 的段
- [x] 6.2 补两个回归用例，把监听根目录放在 `.runtime/` 下（原用例用 `tmp_path` 测不出）
- [x] 6.3 `create_episode` 接受 200/201（Platform 声明的是 201）
- [x] 6.4 新增 `tests/test_platform_client.py` 钉住各端点成功码与 409 重放语义

## 7. 缺陷修复：时间戳与工具链

- [x] 7.1 新增 `UtcDateTime` 列类型，在数据库边界收口时区
- [x] 7.2 替换 5 个模型的 9 个时间列
- [x] 7.3 补 `tests/test_utc_datetime.py`（6 例），验证过它能抓住原缺陷
- [x] 7.4 修 `pnpm-workspace.yaml` 的 `allowBuilds` 占位串，恢复两个前端类型检查
- [x] 7.5 `main.py` 的 demo 用户日志改为实际用户名，不再写死已删的角色

## 8. 工具与验证

- [x] 8.1 `scripts/mock_record.py`：任务名作参数，精确匹配优先于子串
- [x] 8.2 `--list` 标出 topic 与录制器不符的任务；`--force` 验证拒收路径
- [x] 8.3 `.gitignore` 补 `nohup.*.out`
- [x] 8.4 `make check` 全绿
- [x] 8.5 `make demo` 8 条交互跑通到 published
- [x] 8.6 四进程手动验证：文件落地 → 自动走到 `verification_pending`，算子产物落库

## 未完成（不属于本 change）

- [ ] 核验 / 标注 / 审核工作区 —— 依赖 Tool 的播放器与时间轴组件
- [ ] 前端路由 —— 当前用 state 切二级页，刷新回列表、后退键不生效
- [ ] Episode 级数据权限隔离
- [ ] **决定 Episode 状态机是否截短** —— 现在会稳定停在 `verification_pending`，
      「完成」阶段恒为 0。二选一：补人工界面，或以质检通过为终态。此决定尚未做出。
