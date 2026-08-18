# robotdatahub-agent

采集 PC 端：MCAP 录制、分片上传、断电恢复。Python 3.12，裸机/Docker 部署。

## 我依赖 contract 的什么

| 契约项 | 用途 |
|---|---|
| `ws/protocol` | `ws/client.py` 用 `DOWNSTREAM_ADAPTER` 解析下行帧、按契约发上行帧 |
| `schemas.agent.UploadCallback` | 交互③的回调请求体 |
| `schemas.agent.AgentHeartbeat` | 心跳内容 |
| `enums.UploadStatus` | 本地 SQLite 的上传状态取值 |
| `enums.EpisodeStatus` | 状态上报 |

## 我暴露什么

不暴露 API（采集端在客户网络内，不接受入站连接）。对外行为：

- WS 连 Platform：注册、心跳、状态上报、接任务
- 分片上传 MCAP 到对象存储
- HTTP 回调 Platform 报告上传完成

## 我参与哪几条交互

| # | 角色 | 实现位置 |
|---|---|---|
| ① | WS 客户端 | `ws/client.py` |
| ② | 分片上传 | `uploader/chunked.py` |
| ③ | 上传完成回调 | `platform_client.py::report_upload_complete` |

## 断电恢复

**这是 Agent 最要紧的能力** —— 采集现场断电、进程被杀、网络中断都是常态。

做法：每一步先落 SQLite 再执行。`recovery.py` 启动时扫两类残局：

| 残局 | 症状 | 处理 |
|---|---|---|
| 上传没传完 | `upload_status != completed` | 读 `uploaded_parts` 只补缺口，不重传已完成分片 |
| 传完但回调没成功 | `upload_status = completed AND callback_done = 0` | 补发交互③的回调 |

第二类最容易被忽略：文件已经在对象存储里，但 Platform 不知道，Episode 会永远卡在
`uploading`。恢复时必须把它也捞出来。

分片粒度落库（`mark_part_uploaded` 每片一次），因此进程被杀最多重传一片。

## 本地替身

| 生产 | 本地 | 替换点 |
|---|---|---|
| MinIO 分片上传 | 按偏移写本地文件 | `uploader/chunked.py::LocalChunkUploader` |
| 真实 MCAP（二进制） | JSON Lines | `recorder/mcap_writer.py` |
| ROS topic 订阅 | 模拟信号生成 | `recorder/mcap_writer.py::record_simulated_episode` |

模拟信号刻意带上真实采集的特征，让下游算子有东西可算：夹爪中段闭合再张开
（预标注能切出 move/grasp/move）、相机帧带 sharpness/occlusion/motion（质检与关键帧有输入）、
可选注入力矩突变（异常检测能报出来）。

续传逻辑本身是真实的，不是替身 —— 换 MinIO 时改的只有 `_write_part` 与 `complete`。

## 运行

```bash
uv sync
uv run python -m agent.main --task-id <task_id>    # 采集一条
uv run python -m agent.main --recover               # 只跑恢复
```
