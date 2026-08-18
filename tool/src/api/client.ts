/**
 * Platform API 客户端（交互④）。
 *
 * 类型全部来自 contract 生成的 `@contract`，不在前端重写一份 interface ——
 * 后端改字段时这里会编译报错，而不是运行期拿到 undefined。
 */

import {
  CONTRACT_VERSION,
  type Annotation,
  type AnnotationSubmit,
  type Episode,
  type ReviewResult,
  type TokenResponse,
  type VerifyResult,
} from '@contract';

const API_BASE = '/api/v1';

/** 统一响应封套。契约里是 ApiResponse<T>，TS 侧按需展开。 */
interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: { code: string; message: string; field?: string | null } | null;
}

interface PaginatedEnvelope<T> {
  success: boolean;
  data: T[];
  meta: { total: number; page: number; limit: number } | null;
  error: { code: string; message: string } | null;
}

/** API 调用失败。携带机器可读的 code，供 UI 分类处理。 */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

let accessToken: string | null = null;

/** 设置 JWT。登录成功后调用。 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/** 当前契约版本，启动时可与后端 /health 比对。 */
export function contractVersion(): string {
  return CONTRACT_VERSION;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const payload = (await response.json()) as Envelope<T>;

  if (!response.ok || !payload.success) {
    const code = payload.error?.code ?? 'UNKNOWN';
    const message = payload.error?.message ?? `请求失败（${response.status}）`;
    throw new ApiError(code, message, response.status);
  }
  return payload.data as T;
}

async function requestList<T>(path: string): Promise<{ items: T[]; total: number }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  const response = await fetch(`${API_BASE}${path}`, { headers });
  const payload = (await response.json()) as PaginatedEnvelope<T>;
  if (!response.ok || !payload.success) {
    const code = payload.error?.code ?? 'UNKNOWN';
    throw new ApiError(code, payload.error?.message ?? '请求失败', response.status);
  }
  return { items: payload.data, total: payload.meta?.total ?? payload.data.length };
}

/** 登录。 */
export async function login(username: string, password: string): Promise<TokenResponse> {
  const token = await request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  setAccessToken(token.access_token);
  return token;
}

/** 待核验队列。 */
export function fetchVerificationQueue(page = 1, limit = 20) {
  return requestList<Episode>(`/verification/queue?page=${page}&limit=${limit}`);
}

/** 待标注队列。 */
export function fetchAnnotationQueue(page = 1, limit = 20) {
  return requestList<Episode>(`/annotation/queue?page=${page}&limit=${limit}`);
}

/** 待审核队列。 */
export function fetchReviewQueue(page = 1, limit = 20) {
  return requestList<Episode>(`/annotation/review-queue?page=${page}&limit=${limit}`);
}

/** 单个 Episode。 */
export function fetchEpisode(episodeId: string): Promise<Episode> {
  return request<Episode>(`/episodes/${episodeId}`);
}

/** 标注详情。 */
export function fetchAnnotation(episodeId: string): Promise<Annotation> {
  return request<Annotation>(`/annotation/${episodeId}`);
}

/** 提交核验结果。 */
export function submitVerification(result: VerifyResult): Promise<Episode> {
  return request<Episode>(`/verification/${result.episode_id}`, {
    method: 'POST',
    body: JSON.stringify(result),
  });
}

/** 提交标注。segments 是全量分段，不是增量补丁。 */
export function submitAnnotation(submission: AnnotationSubmit): Promise<Annotation> {
  return request<Annotation>(`/annotation/${submission.episode_id}`, {
    method: 'POST',
    body: JSON.stringify(submission),
  });
}

/** 提交审核结果。 */
export function submitReview(result: ReviewResult): Promise<Episode> {
  return request<Episode>(`/annotation/${result.episode_id}/review`, {
    method: 'POST',
    body: JSON.stringify(result),
  });
}
