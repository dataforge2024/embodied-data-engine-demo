# robotdatahub-algo

GPU 推理算子。每个算子是一个独立镜像，由 Scheduler 以 K8s Job 形式动态创建（交互⑦）。

## 我依赖 contract 的什么

| 契约项 | 用途 |
|---|---|
| `enums.AlgoOperator` | 算子标识，与镜像名一一对应 |
| `schemas.episode.Segment` | 预标注算子的输出结构 |
| `schemas.episode.KeyFrame` | 关键帧算子的输出结构 |
| `schemas.episode.QualityReport` | 质检算子的输出结构 |

**不依赖** Scheduler / Platform 的任何代码。算子不碰 K8s API、不直连数据库、不调 Platform ——
它是一个纯粹的「读输入、算、写输出」进程。

## 我暴露什么

四个算子入口，统一的环境变量契约 + JSON 产物：

| 算子 | GPU | 输出字段 | 本阶段实现 |
|---|---|---|---|
| `preannotate` | 1 | `segments` | 按夹爪开合变化点分段（启发式） |
| `quality` | 0 | `quality` | 模糊/遮挡阈值判定（元数据启发式） |
| `keyframe` | 0 | `key_frames` | 运动能量取极值帧 |
| `anomaly` | 1 | `anomalies` | 关节超限/力矩突变/时间戳倒退（规则） |

GPU 需求在 `scheduler/k8s/job_builder.py::GPU_REQUIREMENTS` 声明 —— 质检与关键帧是轻量 CV，
纯 CPU 足够，不占 GPU 配额。

## 运行时契约

Scheduler 注入环境变量，算子读取：

| 环境变量 | 含义 |
|---|---|
| `RDH_JOB_ID` | 作业 ID |
| `RDH_EPISODE_ID` | 待处理 Episode |
| `RDH_OPERATOR` | 算子类型 |
| `RDH_INPUT_PATH` | 输入 MCAP 路径（生产为 MinIO 对象键） |
| `RDH_OUTPUT_DIR` | 产物目录（生产为 MinIO 前缀） |
| `RDH_MODEL_VERSION` | 模型版本（镜像 tag） |

产物写到 `$RDH_OUTPUT_DIR/result.json`，只含本算子负责的业务字段；
`job_id` / `status` / 时间戳等编排字段由 Scheduler 补齐。

## 我参与哪几条交互

| # | 角色 | 实现位置 |
|---|---|---|
| ⑦ | 被 Scheduler 调度执行 | `operators/*/main.py` |

算子是交互⑦的被动方 —— 它不主动联系任何模块。

## 本阶段的实现替代

四个算子都是**可运行的启发式实现**，不是 stub：真实模型换进来时改的只有各
`Operator.process()` 内部，输出契约与执行环境契约不变。

| 算子 | 真实实现 | 本阶段替代 |
|---|---|---|
| preannotate | 时序分割网络（TCN/Transformer）on 关节序列 | 夹爪开合状态变化点 |
| quality | 拉普拉斯方差 + 分割模型 | 帧元数据阈值判定 |
| keyframe | 特征差异 / 显著性检测 | 运动能量极值 |
| anomaly | 自编码器重构误差 | 物理约束规则 |

MCAP 解析同理：真实 MCAP 需 `mcap` 库解二进制容器；demo 里是 JSON Lines
（`src/algo_common/io.py`），保留了 topic / timestamp / 消息体三要素，
所以分段与抽帧逻辑是真实可跑的。

## 运行

```bash
uv sync
uv run pytest                        # 算子单测

# 单独跑一个算子（Scheduler 就是这么调的）
RDH_JOB_ID=j1 RDH_EPISODE_ID=e1 RDH_OPERATOR=quality \
RDH_INPUT_PATH=/path/to/raw.mcap RDH_OUTPUT_DIR=/tmp/out \
  uv run python -m operators.quality.main
```

## 构建镜像

```bash
docker build -f operators/quality/Dockerfile -t robotdatahub/algo-quality:v0.1.0 .
```

镜像 tag 即模型版本。Scheduler 按 `RDH_ALGO_MODEL_VERSION` 选择要跑哪个版本。
