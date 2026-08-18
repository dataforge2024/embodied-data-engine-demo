/**
 * Platform 自有前端的 API 客户端。
 *
 * 与 Tool 的客户端是两份代码（不同仓库），但共用 contract 生成的同一份类型 ——
 * 这是「不重写 interface」的落点。
 */

import { CONTRACT_VERSION, type AgentNode, type CollectTask, type Episode, type TokenResponse } from '@contract';

const API_BASE = '/api/v1';

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
  constructor(readonly code: string, message: string, readonly status: number) {
    super(message);
    this.name = 'ApiError';
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
  const base: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) base.Authorization = `Bearer ${accessToken}`;
  return base;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(payload.error?.code ?? 'UNKNOWN', payload.error?.message ?? '请求失败', response.status);
  }
  return payload.data as T;
}

async function getList<T>(path: string): Promise<{ items: T[]; total: number }> {
  const response = await fetch(`${API_BASE}${path}`, { headers: headers() });
  const payload = (await response.json()) as PaginatedEnvelope<T>;
  if (!response.ok || !payload.success) {
    throw new ApiError(payload.error?.code ?? 'UNKNOWN', payload.error?.message ?? '请求失败', response.status);
  }
  return { items: payload.data, total: payload.meta?.total ?? payload.data.length };
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  });
  const payload = (await response.json()) as Envelope<TokenResponse>;
  if (!response.ok || !payload.success || !payload.data) {
    throw new ApiError(payload.error?.code ?? 'UNAUTHORIZED', payload.error?.message ?? '登录失败', response.status);
  }
  setAccessToken(payload.data.access_token);
  return payload.data;
}

export const fetchTasks = () => getList<CollectTask>('/tasks');
export const fetchEpisodes = (status?: string) =>
  getList<Episode>(`/episodes${status ? `?status=${status}` : ''}`);
export const fetchEpisodeStats = () => get<Record<string, number>>('/episodes/stats');
export const fetchAgents = () => get<AgentNode[]>('/agents');
