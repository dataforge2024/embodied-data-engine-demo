## Context

Agent 当前的主循环是 `await client.wait_task()` — 等 Platform 经 WebSocket 推送任务，然后调用 `Collector.collect_once()` 模拟录制并上传到本地目录。协议层（`contract/src/rdh_contract/ws/protocol.py` 的 10 种帧、`ConnectionManager` 的推送方法、`ChunkedUploader` 的续传逻辑）都已完整且有测试覆盖，缺的是把真实采集产物接进来的机制。

三条关键的既有约束决定了本设计的形状：

**替身分离已经就位。** `ObjectStore` 是 Protocol（`platform/app/services/object_store.py:25`），`LocalObjectStore` 是其中一个实现。换 OSS 只需新增实现类，调用方不动。但 Agent 侧的 `LocalChunkUploader`（`agent/src/agent/uploader/chunked.py:45`）**不是** Protocol，是具体类 — 这是本变更要补的一处不对称。

**契约几乎不用改。** `AgentTaskPush` 已含 `task_id` / `task_name` / `requirement`，建目录与写元数据所需字段齐备；`UploadProgressFrame` 已含 `episode_id` / `uploaded_parts` / `total_parts`。`ws/protocol.py` 一行不动。

**两处代码已经承诺了尚未实现的行为。** `platform/app/api/routes/tasks.py:85` 的注释写明「Agent 重连后会拉取已分派任务」，但该端点不存在。`platform/app/ws/handlers.py:123` 主动选择不落库进度，理由是「高频写会放大 IO」— 在无人观测进度的原设计下成立，在需要实时展示时会导致前端刷新后进度归零。

演示对象是技术团队与产品团队。技术团队关注替身能否换成真实基础设施、架构是否可信；产品团队关注系统是否活着、工作流是否成立。上传进度是同时回答两者的唯一环节 — 技术看到 WS 双向通信在工作，产品看到进度条在动。

**本项目是原型，代码会被丢弃**（见根目录 `CLAUDE.md`）。这对本设计有两处直接影响：实现取最直接的写法，不为假想的扩展点提前抽象；测试只覆盖主流程与少数关键失败路径，边缘 case 在 spec 中记录为预期行为但不逐条写测试。`specs/` 里的 88 个场景因此分为两类 — 主流程与可靠性相关的约 30 个是必须验证的，其余是行为约定，供实现时参照，不构成测试债。

## Goals / Non-Goals

**Goals:**

- 采集人员把 MCAP 文件放入目录即触发上传，无需命令行、无需登录网页
- 任务目录自解释：目录名含任务名与 ID，目录内 `.task.json` 载明采集要求
- 数据真实离开开发机，落到阿里云 OSS
- 上传进度经 WebSocket 实时回传并落库，刷新页面不丢
- Agent 容器重启、WS 断连、断电均可恢复，不丢任务、不重传已完成分片
- 无效上传前置拦截：topic 不达标的文件在本地就拒绝，不浪费带宽
- OSS 凭据不入库（仓库为 public）

**Non-Goals:**

- 前端进度条与任务界面 — 本变更只保证后端数据齐备，UI 属下一期
- RabbitMQ 替换 `FileQueuePublisher` — 单机 demo 下文件队列语义足够，替换价值在分布式
- K8s Job 接入 — Scheduler 继续 `SubprocessRunner`，交互⑦维持模拟
- Agent 自身录制 MCAP — 录制职责移交采集软件，Agent 只负责搬运
- 多 Agent 协同与任务抢占 — `CollectTask.assignments` 支持多 Agent，但本期不处理竞争
- 视频转码与 `SensorStream.preview_url` — 核验界面所需，属第四期

## Decisions

### 1. 目录名格式：`<slug(任务名)>__<task_id>`

三个约束互相拉扯：人要看懂是哪个任务（要任务名）、任务可能同名（要 ID）、文件系统对字符敏感（要转义）。

考虑过的方案：

| 方案 | 样例 | 否决理由 |
|---|---|---|
| 纯 ID | `t-a3f9c1` | 采集人员看不出是哪个任务，目录多了完全无法辨认 |
| 纯任务名 | `厨房抓取放置` | 同名任务冲突；`name` 无字符约束，斜杠会造出子目录 |
| ID + 目录内标记 | `t-a3f9c1/` 内含 `.task.json` | 必须打开文件才知道是什么，`ls` 无信息 |
| **名字 + ID**（采纳） | `厨房抓取-放置-v2__t-a3f9c1` | — |

`TaskCreate.name` 是 `min_length=1, max_length=200` 且无字符约束，因此必须 slugify：

```
"厨房抓取/放置 (v2)"  →  "厨房抓取-放置-v2"
```

规则：路径分隔符与空白折叠为单个 `-`；`( ) [ ] { } < > : " | ? * \` 及控制字符去除；首尾 `-` 与 `.` 去除（避免隐藏目录）；连续 `-` 折叠；截断至 60 字符（含中文按字符计，不按字节）；结果为空时回退为 `task`。

**保留中文**（不转拼音、不转 ASCII）：Agent 容器只跑 Linux，ext4/overlayfs 对 UTF-8 路径无问题，而 `厨房抓取` 对中文使用者的可读性远高于 `chufang-zhuaqu`。代价是若将来 Agent 要跑在 Windows 容器或经过不支持 UTF-8 路径的工具链，需回退纯 ASCII 策略。

**`__` 作分隔符**：双下划线在任务名中几乎不自然出现，而单个 `-` 或 `_` 极常见 — 用单字符分隔会让 `task_id` 的解析产生歧义。解析时从右侧首个 `__` 切分，因此任务名中即便含 `__` 也不影响取 ID。

**目录名不是权威数据源。** `task_id` 同时写在 `.task.json` 里，两者不一致时以 `.task.json` 为准（人可能手动改过目录名）。目录名只是为了 `ls` 时可读。

### 2. 写入完成检测：大小稳定采样 + 标记文件双支持

`watchdog` 在文件**创建时**即触发事件，此时 500MB 的 MCAP 可能只写了几 KB。立即读取会得到残缺文件，checksum 必然不符。这是目录监听最经典的失败模式。

| 方案 | 可靠性 | 否决理由 |
|---|---|---|
| 立即处理 | 低 | 必然读到半个文件 |
| 要求 `.tmp` 后缀，写完改名 | 高 | 依赖人配合；改名是原子的但人未必照做 |
| 大小稳定采样 | 中 | 慢（默认 3 秒）；理论上写入停顿超过采样窗口会误判 |
| 标记文件 | 高 | 依赖上游工具配合，人工拷贝场景不适用 |
| **稳定采样 + 标记文件**（采纳） | 中—高 | — |

默认走大小稳定检测：每秒采样一次 `st_size`，连续 3 次不变视为写完。人什么都不用做。

同时支持标记文件：见到 `<name>.mcap.done` 立即处理对应文件，跳过采样等待。这条为脚本化上游预留 — 采集软件写完后 `touch` 一个标记即可立即触发。

采样间隔与次数经配置暴露（`RDH_STABLE_SAMPLE_INTERVAL_SECONDS` / `RDH_STABLE_SAMPLE_COUNT`），大文件或慢速网络盘可调高。

**已知残余风险**：写入过程若停顿超过 3 秒（网络盘抖动、上游进程被挂起），会被误判为写完。缓解是 MCAP 解析阶段会校验文件结构完整性 — 残缺文件解析失败，进 `.rejected/` 而非被当作有效数据上传。这把「误判」的后果从「上传坏数据」降为「需要人重放一次」。

### 3. MCAP 格式嗅探：不赌单一格式

采集软件产出的可能是标准 MCAP（protobuf/ros2 编码，含真实图像帧），也可能是本项目 `mcap_writer.py` 产出的 JSON Lines 格式（模拟传感器标量）。

原本打算让用户二选一，但赌错任何一边的代价都是重写 reader，而嗅探成本只有十几行：

```
读文件头 8 字节
  \x89MCAP0\r\n  → 标准 MCAP，走 mcap 官方库
  {              → JSON Lines，走现有 algo_common/io.py 的 reader
  其他            → 非法格式，进 .rejected/
```

两条路径都必须产出同样的 `(topics: tuple[str, ...], duration_ms: int)`，由统一的 `McapMetadata` 结构承载，下游（预检、回调）不感知格式差异。

标准 MCAP 分支引入 `mcap` 官方库依赖。若手上暂无真实样本，该分支用构造样本测试 — 库本身提供写入 API，可生成合法的最小 MCAP 用于验证嗅探与 topic 解析。

**副产品**：若上游是标准 MCAP 且含真实图像帧，第四期的核验界面就有真视频可播，`SensorStream.preview_url` 从 null 变为可填。这是格式嗅探的额外回报，但不在本期兑现。

### 4. 采集要求本地预检：省一次无效上传

`TaskRequirement.required_topics` 声明必须录制的 topic，缺失则核验不通过。既然 Agent 解析 MCAP 时已经拿到实际 topic 列表，就可以在上传前比对。

不预检的代价：几百 MB 上传完、进入 processing、跑完算子、到核验环节才被人发现缺 topic。预检把这个反馈从「几分钟 + 一次全量传输」压缩到「秒级 + 零传输」，且采集人员当场就能重录。

预检失败的文件移入 `.rejected/` 并写 `<name>.mcap.error`，内容为缺失的 topic 清单。**不创建 Episode** — 这条数据从未进入平台，不该在状态机里留痕。

预检只校验 topic 存在性，不校验时长（`min_duration_ms` / `max_duration_ms`）。时长是核验环节的人工判断项，且边界情况（刚好差 100ms）由人裁量比机器拒绝更合适。

### 5. 抽取 `ChunkUploader` Protocol

`ObjectStore` 是 Protocol，`LocalChunkUploader` 却是具体类。这处不对称使 OSS 上传无法沿用「换实现不动调用方」的模式。

本变更抽取 Protocol，签名沿用现有 `LocalChunkUploader.upload()`：

```python
class ChunkUploader(Protocol):
    def upload(
        self,
        *,
        source: Path,
        object_key: str,
        already_uploaded: tuple[int, ...] = (),
        on_part_done: Callable[[int], object] | None = None,
    ) -> UploadOutcome: ...
```

`OSSChunkUploader` 实现同一 Protocol，内部调用 `oss2` 的 `init_multipart_upload` / `upload_part` / `complete_multipart_upload`。`already_uploaded` 语义不变（跳过已完成分片），`on_part_done` 语义不变（每片成功后回调，调用方据此落库）。

**保持同步接口。** `oss2` SDK 是同步的，且现有 `LocalChunkUploader.upload()` 也是同步方法。强行改 async 会引入线程池包装且不带来实际并发收益（单文件分片上传本身是串行的，续传语义要求顺序确定）。Agent 主循环是 asyncio，上传调用经 `asyncio.to_thread` 移出事件循环，避免阻塞心跳。

### 6. Agent 直接持有 OSS 凭据

Agent 启动时从环境变量读取 OSS AK/SK + endpoint + bucket，直接上传，不依赖 Platform 签发临时凭据。

**为什么不用 Platform 签发临时凭据**：
- 原型阶段追求极简 — Agent 自治，不依赖 Platform 的凭据下发逻辑
- 避免 HTTP 与 WS 两条通道的时序耦合（Agent 调 `POST /episodes/{id}/start-upload` 后异步等 `UploadGrantFrame`，失败处理复杂）
- 生产场景可以用 RAM 子账号 + 最小权限策略（PutObject/GetObject/AbortMultipartUpload/ListParts 限定单 bucket）控制风险

**凭据配置**：
```
OSS_ACCESS_KEY_ID       # 阿里云 RAM 子账号 AK
OSS_ACCESS_KEY_SECRET   # 对应 SK
OSS_ENDPOINT           # oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET             # robotdatahub-demo
```

经 `.env.oss` 注入（gitignored），`docker-compose.yml` 用 `env_file` 引用。Agent 启动时校验这些变量存在且非空，缺失则拒绝启动。

**tradeoff**：
- ✅ 架构极简，无 Platform 凭据签发、无 HTTP/WS 时序耦合
- ✅ Agent 重启后直接恢复上传，无需重新请求凭据
- ❌ Agent 持有长期凭据（但 RAM 子账号 + 最小权限 + gitignored 已足够）
- ❌ 换 bucket / 轮换密钥需重启容器（原型可接受）

`ObjectStore.issue_grant()` 与 `UploadGrantFrame` 保留但本期不使用 — Platform 若将来需要「主动推送重传任务」，可复用这条路径。

### 7. 进度落库：节流而非丢弃

`handlers.py:123` 现在收到 `UploadProgressFrame` 只写 `logger.debug`，注释说明理由是「高频写会放大 IO」。该顾虑成立 — 256KB 分片、500MB 文件即 2000 片，每片一次 UPDATE 是真实的写放大。

但不落库导致：前端刷新页面后进度归零，而文件仍在传。演示时这个表现比没有进度条更糟。

采纳节流：**WS 帧每片都发**（前端实时更新，无延迟损失），**落库有节流** — 进度变化 ≥5% 或距上次写入 ≥2 秒才 UPDATE。2000 片经节流降至约 20 次写入，写放大问题消解，而刷新后可从库中恢复进度。

节流状态存在 `ConnectionManager` 的连接对象上（内存），不额外引入存储。连接断开即丢弃 — 重连后第一帧必然触发一次写入，因为「距上次写入」已超阈值。

`Episode.upload_progress` 用 `float`（0.0–1.0）而非分片计数：前端只需百分比，且分片数随 `chunk_size` 配置变化，比例更稳定。

### 8. 文件生命周期：子目录标记，不删原文件

```
/watch/厨房抓取__t-a3f9c1/
├── .task.json                    任务快照 + requirement
├── ep_003.mcap                   待检测 / 待上传
├── .uploading/
│   └── ep_002.mcap               正在传（含分片状态，断电后据此续传）
├── .done/
│   └── ep_001.mcap               传完归档
├── .failed/
│   ├── ep_bad.mcap               上传失败
│   └── ep_bad.mcap.error         失败原因
├── .rejected/
│   ├── ep_notopic.mcap           预检不通过
│   └── ep_notopic.mcap.error     缺失的 topic 清单
└── .cancelled/
    └── ep_x.mcap                 任务取消时未开始上传的文件
```

单文件状态机：

```
     人放入
       │
       ▼
  ┌─────────┐  大小稳定/见标记  ┌─────────┐
  │ 待检测   ├────────────────▶│ 待解析   │
  └────┬────┘                  └────┬────┘
       │ 一直在变                    │
       ▼                            │ 格式非法或结构残缺
   （继续采样）                      ├──────────────▶ .rejected/
                                    │ topic 不达标
                                    ├──────────────▶ .rejected/
                                    │ 通过
                                    ▼
                              ┌──────────┐  建 Episode  ┌───────────┐
                              │ 待上传    ├────────────▶│ .uploading/│
                              └──────────┘             └─────┬─────┘
                                                             │ 分片重试耗尽
                                                             ├────▶ .failed/
                                                             │ 全片成功 + 回调成功
                                                             ▼
                                                        ┌────────┐
                                                        │ .done/ │
                                                        └────────┘
```

**归档而非删除**：采集人员可能需要核对本地文件与云端一致性，删掉就无从查证。代价是磁盘占用 — 经 `RDH_KEEP_UPLOADED=false` 可切为传完即删，默认保留。

**`.failed/` 必须附 `.error` 文件**：否则人只能对着一个失败文件干瞪眼。内容包含失败阶段、错误类型、最后一次错误信息。

**点号前缀**：使这些子目录在 `ls` 中不显眼，也让 watchdog 的过滤规则简单 — 只处理任务目录顶层的 `*.mcap`，忽略所有点号开头的路径。

### 9. Agent 重启后重建目录：新增 `GET /agents/me/tasks`

`tasks.py:85` 的注释已承诺此行为，端点从未实现。三种补法：

| 方案 | 否决理由 |
|---|---|
| Platform 维护待推送队列，Agent 重连时补推 | 需要持久化未送达消息，引入新存储关注点；且解决不了「容器重建后 volume 为空」 |
| 分派失败直接报错，让 Admin 重试 | 把系统的健壮性问题转嫁给操作人 |
| **Agent 主动拉取**（采纳） | 一个幂等 GET 解决两个场景 |

采纳主动拉取。Agent 在两个时机调用：进程启动时、WS 重连成功后。返回该 Agent 所有 `assigned` 状态的任务，Agent 据此**幂等地**确保目录存在（已存在则只校验 `.task.json` 是否需要更新，不覆盖已有文件）。

这同时覆盖了「容器重建、`/watch` volume 为空」的场景 — 演示现场重启一次容器不会丢任务。

鉴权用 Agent token（与 `/callbacks/upload-complete` 一致），路径中的 `me` 由 token 解析出的 agent 身份决定，不接受路径参数指定他人 — 避免一个 Agent 探知另一个 Agent 的任务。

### 10. 任务完成与取消的目录处置

**达成 `target_episode_count`**：目录改名追加 `__已完成`，不阻止继续放文件。删除人辛苦录制的数据过于激进；纯粹放任则采集人员不知何时该停。改名是视觉告知，成本最低。

此处有个语义不一致需要记录：`CollectTask.progress_ratio` 用 `published_count / target`，即 `target_episode_count` 是**目标发布数**。但采集端无法预知哪些会被核验打回 — 采 20 条可能只发布 15 条。严格实现需要 Platform 判断达成后推送新帧通知 Agent，契约需增帧。**本期简化**：Agent 按自己成功上传的条数计数，不追踪发布结果。差异在 `.task.json` 的 `progress` 字段中如实标注为 `uploaded`，不冒充 `published`。

**收到 `TaskCancelFrame`**：已开始上传的文件**传完**（中断会在 OSS 留下未完成分片），未开始的移入 `.cancelled/`，目录改名追加 `__已取消`。

### 11. OSS 凭据与成本

仓库是 public，AK/SK 绝不能进任何提交的文件。

```
.env.oss              gitignored，实际凭据
.env.oss.example      提交，仅键名与说明
docker-compose.yml    提交，只写 env_file: .env.oss
```

**要求使用 RAM 子账号**，策略仅授权目标 bucket 的 `PutObject` / `GetObject` / `AbortMultipartUpload` / `ListParts`。不使用主账号 AK — 泄漏时损失可控。

**OSS 须配置「清理未完成分片」生命周期规则。** 阿里云对未完成的分片上传持续计存储费用，失败的上传会静默积累成本。此项无法由代码解决，须在 OSS 控制台配置，写入部署文档。

现有 `assert_production_ready()` 的默认凭据检查扩展覆盖 OSS 相关变量 — 生产环境缺失 OSS 配置时拒绝启动。

### 12. 前置修复两项

**运行时目录统一**：`scripts/demo.py:43` 写 `.runtime/demo`，`platform/app/core/config.py:15` 默认 `.runtime`。两个独立的 SQLite 库，demo 产生的数据对手工启动的服务完全不可见 — 手工起服务查 Episode 得到 0 条。统一为单一常量，demo 经环境变量覆盖到子目录（保留 `make clean-runtime` 不影响手工数据的能力），或直接共用。选前者：隔离性有价值，但必须是显式的、文档化的，而非两个默认值悄然分叉。

**用户种子数据**：`AuthService.create_user()` 存在但无调用方，无任何用户记录，登录必然失败（实测 `POST /auth/login` 返回 `UNAUTHORIZED`）。Agent 的 `platform.login()` 同样失败。补幂等 seed：各角色一个 demo 用户，密码经环境变量注入且 `assert_production_ready()` 覆盖，生产环境不创建。

## Risks / Trade-offs

**[写入完成误判] 文件写入停顿超过采样窗口，残缺文件被当作完整** → MCAP 解析阶段校验结构完整性，残缺文件解析失败进 `.rejected/`，后果从「上传坏数据」降为「需人工重放」。采样参数可配置，慢速存储可调高。

**[目录名与 `task_id` 不一致] 人手动改了目录名** → `.task.json` 为权威源，目录名仅供阅读。解析优先读 `.task.json`，缺失或损坏时才从目录名回退取 ID。

**[中文路径] 若 Agent 将来需跑 Windows 容器或经过不支持 UTF-8 路径的工具链** → 当前明确限定 Linux 容器。slugify 实现集中在单一函数，切换 ASCII 策略只改该函数与其测试。

**[标准 MCAP 分支缺真实样本验证] 嗅探与解析可能与真实文件不符** → 用 `mcap` 官方库的写入 API 构造样本测试，覆盖 magic bytes 与 topic 解析；获得真实样本后补充验证用例。风险实质是「标准 MCAP 分支可能首次接真实数据时暴露问题」，但格式嗅探保证了另一分支不受影响。

**[OSS 分片成本] 失败的上传留下未完成分片，持续计费** → 生命周期规则清理（控制台配置，写入部署文档）；`TaskCancelFrame` 处理时让进行中的上传自然完成而非中断。

**[凭据泄漏] public 仓库** → `.env.oss` gitignored；RAM 子账号最小权限；`.env.oss.example` 只含键名；`assert_production_ready()` 覆盖 OSS 变量。

**[进度节流的可见延迟] 落库滞后最多 2 秒或 5% 进度** → WS 帧无节流，实时性由 WS 保证；落库只服务「刷新后恢复」，2 秒滞后在该场景下不可感知。

**[e2e 用例受主循环变更影响] Agent 启动语义变更为 BREAKING** → `--task-id` 模拟采集模式保留，既有 7 个用例可继续驱动；新增目录监听链路用例。两套并存直到目录路径稳定。

**[上传阻塞事件循环] `oss2` 同步 SDK 在 asyncio 主循环中** → 经 `asyncio.to_thread` 移出事件循环，心跳与 WS 收发不受影响。代价是每次上传占用一个线程，单 Agent 并发上传数受线程池限制 — 本期不做并发上传，串行处理队列。

**[范围收缩风险] 本期后端数据齐备但无 UI，演示价值不完整** → 明确接受：进度落库与 WS 回传是 UI 的前置，本期交付「链路真实可用」，下一期交付「可见」。若需提前演示，`.task.json` 与 OSS 控制台可作为中间态证据。

## Migration Plan

无生产环境，无数据迁移负担。本地部署步骤：

1. `contract` bump 至 0.2.0，重跑 `make contract-gen`，各模块 `pyproject.toml` 的钉版本同步更新（`contract_checks` 会校验一致性）
2. Platform 侧：新增用户 seed、运行时目录常量统一、`Episode.upload_progress` 迁移（Alembic）、新增端点
3. Agent 侧：新依赖 `uv sync`，主循环切换，`.env.oss` 填入凭据
4. OSS 控制台：创建 bucket、RAM 子账号与策略、未完成分片生命周期规则
5. `docker compose up` 起 Agent 容器，volume 挂载 `/watch`

回退：Agent 主循环保留 `--task-id` 路径，可退回模拟采集；`ObjectStore` / `ChunkUploader` 均为 Protocol，`RDH_OBJECT_STORE_BACKEND=local` 切回本地替身，无需改码。

## Open Questions

- **OSS 接入参数** — bucket 名与 endpoint 区域待提供。AK/SK 经 `.env.oss` 由使用者本地填写，不进入对话或仓库。
- **真实 MCAP 样本** — 有样本可验证嗅探与 topic 解析确实匹配真实文件；无样本则标准 MCAP 分支仅经构造样本测试。
- **`target_episode_count` 的权威判定** — 本期由 Agent 按上传数近似。若需精确按发布数判定，需契约增帧（Platform → Agent 的任务达成通知），留待后续 change。
- **多 Agent 分派同一任务时的文件名冲突** — `object_key` 含 `episode_id`（UUID）故不冲突，但两台机器可能都放 `ep_001.mcap`，日志中难以区分来源。是否在 `object_key` 或日志中带 `agent_id`，待多 Agent 场景真实出现时再定。
