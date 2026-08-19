/**
 * RobotDataHub 契约类型 —— 自动生成，请勿手改。
 *
 * 生成命令：make contract-gen
 * 来源：contract/src/rdh_contract/
 * 契约版本：0.1.0
 */

export const CONTRACT_VERSION = "0.1.0";

// ---- 枚举 ----

export type EpisodeStatus =
  | "recording"
  | "uploading"
  | "uploaded"
  | "processing"
  | "verification_pending"
  | "annotation_pending"
  | "annotation_review"
  | "published"
  | "rejected"
  | "failed";

export const EpisodeStatusValues: readonly EpisodeStatus[] = [
  "recording",
  "uploading",
  "uploaded",
  "processing",
  "verification_pending",
  "annotation_pending",
  "annotation_review",
  "published",
  "rejected",
  "failed",
];

export type TaskStatus =
  | "draft"
  | "published"
  | "assigned"
  | "in_progress"
  | "completed"
  | "cancelled";

export const TaskStatusValues: readonly TaskStatus[] = [
  "draft",
  "published",
  "assigned",
  "in_progress",
  "completed",
  "cancelled",
];

export type Role =
  | "admin"
  | "recorder"
  | "verifier"
  | "annotator"
  | "reviewer"
  | "lab"
  | "sysops";

export const RoleValues: readonly Role[] = [
  "admin",
  "recorder",
  "verifier",
  "annotator",
  "reviewer",
  "lab",
  "sysops",
];

export type JobType =
  | "ingest"
  | "tool"
  | "algo"
  | "notify";

export const JobTypeValues: readonly JobType[] = [
  "ingest",
  "tool",
  "algo",
  "notify",
];

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "timeout";

export const JobStatusValues: readonly JobStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "timeout",
];

export type AlgoOperator =
  | "preannotate"
  | "quality"
  | "keyframe"
  | "anomaly";

export const AlgoOperatorValues: readonly AlgoOperator[] = [
  "preannotate",
  "quality",
  "keyframe",
  "anomaly",
];

export type UploadStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "failed";

export const UploadStatusValues: readonly UploadStatus[] = [
  "pending",
  "in_progress",
  "completed",
  "failed",
];

export type ReviewDecision =
  | "approve"
  | "reject";

export const ReviewDecisionValues: readonly ReviewDecision[] = [
  "approve",
  "reject",
];

// ---- 状态机 ----

/** Episode 合法状态迁移。权威定义在 rdh_contract.state_machine，勿手改。 */
export const EPISODE_TRANSITIONS: Readonly<
  Record<EpisodeStatus, readonly EpisodeStatus[]>
> = {
  "annotation_pending": ["annotation_review", "rejected"],
  "annotation_review": ["annotation_pending", "published", "rejected"],
  "failed": [],
  "processing": ["failed", "verification_pending"],
  "published": [],
  "recording": ["failed", "uploading"],
  "rejected": [],
  "uploaded": ["failed", "processing"],
  "uploading": ["failed", "uploaded"],
  "verification_pending": ["annotation_pending", "rejected"],
};

/** 终态：无出边。 */
export const TERMINAL_EPISODE_STATES: readonly EpisodeStatus[] = ["failed", "published", "rejected"];

/** 判断状态迁移是否合法。前端据此禁用非法操作按钮。 */
export function canTransition(
  source: EpisodeStatus,
  target: EpisodeStatus,
): boolean {
  return EPISODE_TRANSITIONS[source].some((s) => s === target);
}

/** 判断是否为终态。 */
export function isTerminal(status: EpisodeStatus): boolean {
  return TERMINAL_EPISODE_STATES.some((s) => s === status);
}

// ---- 事件 ----

/** RabbitMQ 事件 routing key。 */
export type EventRoutingKey =
  | "annotation.approved"
  | "dataset.build_requested"
  | "episode.rejected"
  | "episode.uploaded";

export const EVENT_ROUTING_KEYS: readonly EventRoutingKey[] = [
  "annotation.approved",
  "dataset.build_requested",
  "episode.rejected",
  "episode.uploaded",
];

// ---- 数据模型 ----

/** 错误详情。 */
export interface ErrorDetail {
  /** 机器可读错误码，如 EPISODE_NOT_FOUND */
  code: string;
  /** 面向用户的错误描述 */
  message: string;
  /** 校验失败的字段名 */
  field?: string | null;
  /** 关联服务端日志的追踪 ID */
  trace_id?: string | null;
}

/** 分页元数据。 */
export interface PageMeta {
  /** 符合条件的总记录数 */
  total: number;
  /** 当前页码，从 1 开始 */
  page: number;
  /** 每页记录数 */
  limit: number;
}

/** MCAP 内的一路传感器流。 */
export interface SensorStream {
  /** MCAP topic 名，如 /camera/front/image_raw */
  topic: string;
  /** 流类型：camera / joint_state / tactile / audio */
  kind: string;
  /** 消息条数 */
  message_count: number;
  /** 采样频率 */
  frequency_hz?: number | null;
  /** 相对 Episode 起点的偏移，多路同步回放用 */
  start_offset_ms?: number;
  /** 转码后的预览视频地址（相机流） */
  preview_url?: string | null;
}

/** 关键帧。由 ingest-worker 抽取或 keyframe 算子识别。 */
export interface KeyFrame {
  /** 相对 Episode 起点的毫秒时间戳 */
  timestamp_ms: number;
  /** 来源 topic */
  topic: string;
  /** MinIO 对象键（抽帧图片） */
  object_key: string;
  /** 关键帧显著性得分 */
  score?: number | null;
}

/** 动作分段。 */
export interface Segment {
  /** 分段 ID（UUID） */
  segment_id: string;
  /** 起始毫秒偏移 */
  start_ms: number;
  /** 结束毫秒偏移，必须大于 start_ms */
  end_ms: number;
  /** 动作标签，如 grasp / place */
  action_label?: string | null;
  /** 自然语言动作描述 */
  description?: string | null;
  /** 算子来源；None 表示人工创建或人工修改 */
  source?: AlgoOperator | null;
  /** 算子置信度 */
  confidence?: number | null;
}

/** 质检算子输出。 */
export interface QualityReport {
  /** 是否通过质检 */
  passed: boolean;
  /** 模糊程度，越大越模糊 */
  blur_score?: number | null;
  /** 遮挡程度 */
  occlusion_score?: number | null;
  /** 问题清单 */
  issues?: string[];
}

/** Episode 完整视图。 */
export interface Episode {
  /** Episode ID（UUID） */
  episode_id: string;
  /** 所属采集任务 ID */
  task_id: string;
  /** 采集来源 Agent ID */
  agent_id: string;
  /** 采集员 user_id；Agent 无人值守采集时为 None */
  recorded_by?: string | null;
  /** 当前状态 */
  status: EpisodeStatus;
  /** MinIO 中的 MCAP 对象键 */
  object_key?: string | null;
  /** MCAP 文件大小 */
  size_bytes?: number | null;
  /** 采集时长 */
  duration_ms?: number | null;
  /** MCAP 的 SHA-256，上传完整性校验 */
  checksum?: string | null;
  /** 传感器流索引，ingest 产出 */
  streams?: SensorStream[];
  /** 关键帧 */
  key_frames?: KeyFrame[];
  /** 动作分段 */
  segments?: Segment[];
  /** 质检结果 */
  quality?: QualityReport | null;
  /** 机器人型号 */
  robot_model?: string | null;
  /** 采集场景标识 */
  scene?: string | null;
  /** 打回或失败原因 */
  reject_reason?: string | null;
  /** 创建时间（UTC） */
  created_at: string;
  /** 最后更新时间（UTC） */
  updated_at: string;
}

/** 创建 Episode（Agent 开始录制时上报）。 */
export interface EpisodeCreate {
  /** 所属采集任务 ID */
  task_id: string;
  /** 采集 PC 的 Agent ID */
  agent_id: string;
  /** 采集员 user_id；Agent 无人值守采集时为 None */
  recorded_by?: string | null;
  /** Agent 本地 MCAP 路径，用于断电恢复定位 */
  local_path: string;
  /** 机器人型号 */
  robot_model?: string | null;
  /** 采集场景标识 */
  scene?: string | null;
}

/** 采集要求。Agent 据此配置录制，Tool 核验时据此判断是否达标。 */
export interface TaskRequirement {
  /** 指定机器人型号 */
  robot_model: string;
  /** 采集场景 */
  scene: string;
  /** 必须录制的 topic，缺失则核验不通过 */
  required_topics: string[];
  /** 单条 Episode 最短时长 */
  min_duration_ms: number;
  /** 单条 Episode 最长时长 */
  max_duration_ms: number;
  /** 目标采集条数 */
  target_episode_count: number;
}

/** 任务分派记录：把任务指派给某个 Agent 节点。 */
export interface TaskAssignment {
  /** 任务 ID */
  task_id: string;
  /** 被指派的 Agent ID */
  agent_id: string;
  /** 操作人 user_id */
  assigned_by: string;
  /** 指派时间（UTC） */
  assigned_at: string;
}

/** 创建采集任务（Admin）。 */
export interface TaskCreate {
  /** 任务名 */
  name: string;
  /** 任务说明 */
  description?: string | null;
  /** 采集要求 */
  requirement: TaskRequirement;
}

/** 采集任务完整视图。 */
export interface CollectTask {
  /** 任务 ID（UUID） */
  task_id: string;
  /** 任务名 */
  name: string;
  /** 任务说明 */
  description?: string | null;
  /** 任务状态 */
  status: TaskStatus;
  /** 采集要求 */
  requirement: TaskRequirement;
  /** 已采集条数 */
  collected_count?: number;
  /** 已发布条数 */
  published_count?: number;
  /** 分派记录 */
  assignments?: TaskAssignment[];
  /** 创建人 user_id */
  created_by: string;
  /** 创建时间（UTC） */
  created_at: string;
  /** 最后更新时间（UTC） */
  updated_at: string;
}

/** 核验结果。 */
export interface VerifyResult {
  /** 被核验的 Episode ID */
  episode_id: string;
  /** 裁决：通过 / 打回 */
  decision: ReviewDecision;
  /** 打回原因 */
  reason?: string | null;
  /** 已核验的 topic */
  checked_topics?: string[];
  /** 核验人 user_id */
  verified_by: string;
  /** 核验时间（UTC） */
  verified_at: string;
}

/** 提交标注（Tool → Platform）。 */
export interface AnnotationSubmit {
  /** 被标注的 Episode ID */
  episode_id: string;
  /** 编辑后的全量分段 */
  segments: Segment[];
  /** 标注备注 */
  notes?: string | null;
}

/** 标注审核结果。 */
export interface ReviewResult {
  /** 被审核的 Episode ID */
  episode_id: string;
  /** 裁决：通过 / 退回重做 */
  decision: ReviewDecision;
  /** 退回原因 */
  reason?: string | null;
  /** 审核人 user_id */
  reviewed_by: string;
  /** 审核时间（UTC） */
  reviewed_at: string;
}

/** 标注记录完整视图（含核验与审核轨迹）。 */
export interface Annotation {
  /** 标注记录 ID（UUID） */
  annotation_id: string;
  /** 所属 Episode ID */
  episode_id: string;
  /** 当前分段 */
  segments?: Segment[];
  /** 标注备注 */
  notes?: string | null;
  /** 核验结果 */
  verify_result?: VerifyResult | null;
  /** 最近一次审核结果 */
  review_result?: ReviewResult | null;
  /** 修订版本，每次退回重做后 +1 */
  revision?: number;
  /** 标注人 user_id */
  annotated_by?: string | null;
  /** 创建时间（UTC） */
  created_at: string;
  /** 最后更新时间（UTC） */
  updated_at: string;
}

/** Agent 心跳（WS 上行，交互①）。 */
export interface AgentHeartbeat {
  /** Agent ID */
  agent_id: string;
  /** Agent 版本号 */
  version: string;
  /** 上报时间（UTC） */
  reported_at: string;
  /** 正在录制的 Episode ID */
  recording_episode_id?: string | null;
  /** 待上传队列长度 */
  pending_upload_count?: number;
  /** 剩余磁盘空间 */
  disk_free_bytes: number;
  /** CPU 占用 */
  cpu_percent?: number | null;
}

/** 任务推送（WS 下行，交互①）。 */
export interface AgentTaskPush {
  /** 任务 ID */
  task_id: string;
  /** 任务名 */
  task_name: string;
  /** 采集要求，Agent 据此配置录制 */
  requirement: TaskRequirement;
  /** 推送时间（UTC） */
  pushed_at: string;
}

/** Agent 节点视图（SysOps 工作区）。 */
export interface AgentNode {
  /** Agent ID */
  agent_id: string;
  /** 主机名 */
  hostname: string;
  /** Agent 版本 */
  version: string;
  /** 是否在线（由心跳超时判定） */
  online: boolean;
  /** 最近一次心跳 */
  last_heartbeat?: AgentHeartbeat | null;
  /** 已分派的任务 */
  assigned_task_ids?: string[];
  /** 首次注册时间（UTC） */
  registered_at: string;
}

/** 分片上传进度（交互②）。 */
export interface UploadProgress {
  /** Episode ID */
  episode_id: string;
  /** MinIO 目标对象键 */
  object_key: string;
  /** MinIO multipart upload ID */
  upload_id?: string | null;
  /** 总分片数 */
  total_parts: number;
  /** 已完成的分片序号（从 1 开始） */
  uploaded_parts?: number[];
  /** 上传状态 */
  status: UploadStatus;
  /** 最近一次失败原因 */
  last_error?: string | null;
}

/** 上传完成回调（交互③，Agent → ``POST /callbacks/upload-complete``）。 */
export interface UploadCallback {
  /** Episode ID */
  episode_id: string;
  /** MinIO 对象键 */
  object_key: string;
  /** 文件大小 */
  size_bytes: number;
  /** SHA-256，Platform 侧校验完整性 */
  checksum: string;
  /** 采集时长 */
  duration_ms: number;
  /** 实际录制到的 topic */
  recorded_topics: string[];
  /** 上传完成时间（UTC） */
  completed_at: string;
}

/** 算子作业参数（Scheduler → K8s Job，交互⑦）。 */
export interface AlgoJobSpec {
  /** 作业 ID（UUID），同时作为 K8s Job 名后缀 */
  job_id: string;
  /** 待处理的 Episode ID */
  episode_id: string;
  /** 算子类型 */
  operator: AlgoOperator;
  /** 算子镜像，含 tag —— tag 即模型版本 */
  image: string;
  /** 输入 MCAP 的 MinIO 对象键 */
  input_object_key: string;
  /** 输出产物的 MinIO 前缀 */
  output_prefix: string;
  /** GPU 数量，0 表示纯 CPU 算子 */
  gpu_count?: number;
  /** 超时时间 */
  timeout_seconds?: number;
  /** Job 完成后的自动清理延迟 */
  ttl_seconds?: number;
}

/** 算子输出（Algo → MinIO，Scheduler 读取）。 */
export interface AlgoJobResult {
  /** 作业 ID */
  job_id: string;
  /** Episode ID */
  episode_id: string;
  /** 算子类型 */
  operator: AlgoOperator;
  /** 作业最终状态 */
  status: JobStatus;
  /** 模型版本（镜像 tag） */
  model_version: string;
  /** 预标注分段 */
  segments?: Segment[];
  /** 关键帧 */
  key_frames?: KeyFrame[];
  /** 质检报告 */
  quality?: QualityReport | null;
  /** 异常描述 */
  anomalies?: string[];
  /** 失败原因 */
  error_message?: string | null;
  /** 开始时间（UTC） */
  started_at: string;
  /** 结束时间（UTC） */
  finished_at: string;
}

/** 算子结果回调（交互⑧，Scheduler → ``POST /callbacks/algo-result``）。 */
export interface AlgoResultCallback {
  /** Episode ID */
  episode_id: string;
  /** 本批算子结果 */
  results: AlgoJobResult[];
  /** 整条流水线是否已完成；仅为 True 时 Platform 才推进 Episode 状态 */
  pipeline_complete: boolean;
  /** 回调时间（UTC） */
  reported_at: string;
}

/** 用户视图。 */
export interface User {
  /** 用户 ID（UUID） */
  user_id: string;
  /** 登录名 */
  username: string;
  /** 展示名 */
  display_name: string;
  /** 角色列表，决定可访问的工作区 */
  roles: Role[];
  /** 是否启用 */
  active?: boolean;
  /** 创建时间（UTC） */
  created_at: string;
}

/** 登录请求。 */
export interface LoginRequest {
  /** 登录名 */
  username: string;
  /** 密码，仅在传输中出现，不落任何日志 */
  password: string;
}

/** 登录响应。 */
export interface TokenResponse {
  /** JWT */
  access_token: string;
  /** Token 类型 */
  token_type?: string;
  /** 有效期（秒） */
  expires_in: number;
  /** 当前用户 */
  user: User;
}

/** Agent 上下线通知。 */
export interface ConsoleAgentStatusFrame {
  type?: "console.agent_status";
  /** Agent ID */
  agent_id: string;
  /** 是否在线 */
  online: boolean;
  /** 主机名，注册时带上 */
  hostname?: string | null;
  /** 状态变更时刻（UTC） */
  at: string;
}

/** 上传进度推送。 */
export interface ConsoleUploadProgressFrame {
  type?: "console.upload_progress";
  /** Episode ID */
  episode_id: string;
  /** 来源 Agent ID */
  agent_id: string;
  /** 已完成分片数 */
  uploaded_parts: number;
  /** 总分片数 */
  total_parts: number;
  /** 百分比，由分片数算出 */
  percent: number;
}
