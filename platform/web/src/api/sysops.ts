import { post } from "./client";

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
