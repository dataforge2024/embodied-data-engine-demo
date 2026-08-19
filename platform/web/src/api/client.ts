/**
 * Platform 自有前端的 API 客户端。
 *
 * 与 Tool 的客户端是两份代码（不同仓库），但共用 contract 生成的同一份类型 ——
 * 这是「不重写 interface」的落点。
 */

import {
  CONTRACT_VERSION,
  type AgentNode,
  type CollectTask,
  type Episode,
  type TokenResponse,
  type User,
} from "@contract";

const API_BASE = "/api/v1";

interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: { code: string; message: string } | null;
}

interface PaginatedEnvelope<T> {
  success: boolean;
  data: T[];
  meta: { total: number; page: number; limit: number } | null;
  error: { code: string; message: string } | null;
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function contractVersion(): string {
  return CONTRACT_VERSION;
}

function headers(): Record<string, string> {
  const base: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) base.Authorization = `Bearer ${accessToken}`;
  return base;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(
      payload.error?.code ?? "UNKNOWN",
      payload.error?.message ?? "请求失败",
      response.status,
    );
  }
  return payload.data as T;
}

async function getList<T>(
  path: string,
): Promise<{ items: T[]; total: number }> {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  const payload = (await response.json()) as PaginatedEnvelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(
      payload.error?.code ?? "UNKNOWN",
      payload.error?.message ?? "请求失败",
      response.status,
    );
  }
  return {
    items: payload.data,
    total: payload.meta?.total ?? payload.data.length,
  };
}

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const payload = (await response.json()) as Envelope<TokenResponse>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new ApiError(
      payload.error?.code ?? "UNAUTHORIZED",
      payload.error?.message ?? "登录失败",
      response.status,
    );
  }
  setAccessToken(payload.data.access_token);
  return payload.data;
}

async function post<T>(path: string, body: any): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(
      payload.error?.code ?? "UNKNOWN",
      payload.error?.message ?? "请求失败",
      response.status,
    );
  }
  return payload.data as T;
}

export { get, post };

export const fetchTasks = () => getList<CollectTask>("/tasks");
export const fetchTask = (taskId: string) =>
  get<CollectTask>(`/tasks/${taskId}`);

/** 采集记录。传 taskId 只取某个任务下的（任务详情用）。 */
export const fetchEpisodes = (params?: {
  status?: string;
  taskId?: string;
}) => {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.taskId) query.set("task_id", params.taskId);
  const suffix = query.toString();
  return getList<Episode>(`/episodes${suffix ? `?${suffix}` : ""}`);
};
export const fetchEpisodeStats = () =>
  get<Record<string, number>>("/episodes/stats");
export const fetchAgents = () => get<AgentNode[]>("/agents");
export const fetchOnlineAgents = () => get<string[]>("/agents/online");
export const fetchUsers = () => get<User[]>("/users");

export async function createTask(data: {
  name: string;
  description?: string | null;
  requirement: {
    robot_model: string;
    scene: string;
    required_topics: string[];
    min_duration_ms: number;
    max_duration_ms: number;
    target_episode_count: number;
  };
}): Promise<CollectTask> {
  return post<CollectTask>("/tasks", data);
}

export async function assignTask(
  taskId: string,
  agentId: string,
): Promise<void> {
  await post(`/tasks/${taskId}/assign`, { agent_id: agentId });
}
