import { get, post } from "./client";

/** 单个队列的巡检结果。字段与后端 `QueueDepth` 对应。 */
export interface QueueDepth {
  queue: string;
  pending: number;
  routing_keys: string[];
  /** 队列是否已被 Scheduler 声明过。未起 worker 时为 false，不是错误。 */
  reachable: boolean;
}

/** 一次队列巡检。字段与后端 `QueueSnapshot` 对应。 */
export interface QueueSnapshot {
  backend: "file" | "rabbit";
  queues: QueueDepth[];
  dlq_count: number;
  exchange: string;
  dlx: string;
  /** 脱敏后的 broker 地址；file 后端为 null。 */
  broker: string | null;
  /** 巡检失败原因（如 broker 不可达）。非 null 时页面显示告警而非报错。 */
  error: string | null;
}

export const fetchQueues = () => get<QueueSnapshot>("/sysops/queues");

export async function triggerUpload(
  agentId: string,
  taskId?: string | null,
  reason?: string | null,
): Promise<void> {
  await post<{ sent: boolean }>("/sysops/trigger-upload", {
    agent_id: agentId,
    task_id: taskId ?? null,
    reason: reason ?? null,
  });
}
