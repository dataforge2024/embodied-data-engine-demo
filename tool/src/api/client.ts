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
  type User,
  type VerifyResult,
} from "@contract";

const API_BASE = "/api/v1";

/** 登录端点。它的 401 是「密码错」，不是「会话过期」，不该触发登出流程。 */
const LOGIN_PATH = "/auth/login";

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
    this.name = "ApiError";
  }
}

const TOKEN_KEY = "rdh.tool.token";
const USER_KEY = "rdh.tool.user";

let accessToken: string | null = null;

/** 凭据失效时的回调。由 App 注册，用于回登录页。 */
let onUnauthorized: (() => void) | null = null;

/** 注册 401 处理。App 挂载时调用一次。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** 设置 JWT。登录成功后调用；传 null 表示登出。 */
export function setAccessToken(token: string | null): void {
  accessToken = token;
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
  } else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }
}

/**
 * 从 localStorage 恢复会话。
 *
 * 刷新页面不该被登出。token 是否仍有效由后端说话 —— 这里只恢复，第一个业务请求
 * 撞 401 时会被清掉。
 */
export function restoreSession(): User | null {
  const token = localStorage.getItem(TOKEN_KEY);
  const raw = localStorage.getItem(USER_KEY);
  if (!token || !raw) return null;
  accessToken = token;
  try {
    return JSON.parse(raw) as User;
  } catch {
    // 存的内容坏了，当作没登录 —— 别让一个坏 JSON 把应用卡在白屏
    setAccessToken(null);
    return null;
  }
}

/** 登出：清凭据并回登录页。 */
export function logout(): void {
  setAccessToken(null);
  onUnauthorized?.();
}

/** 当前契约版本，启动时可与后端 /health 比对。 */
export function contractVersion(): string {
  return CONTRACT_VERSION;
}

/**
 * 凭据失效的统一处置：清 token 并通知 App 回登录页。
 *
 * **清 token 是关键** —— 不清的话后续每个请求都带着同一个过期凭据再撞一次 401，
 * 用户看到的是一串失败而不是一个登录页。
 */
function handleUnauthorized(path: string): void {
  if (path === LOGIN_PATH) return;
  setAccessToken(null);
  onUnauthorized?.();
}

/** 带上凭据的请求头。 */
function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(extra ?? {}),
  };
  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = authHeaders(init?.headers as Record<string, string>);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const payload = (await response.json()) as Envelope<T>;

  if (!response.ok || !payload.success) {
    if (response.status === 401) handleUnauthorized(path);
    const code = payload.error?.code ?? "UNKNOWN";
    const message = payload.error?.message ?? `请求失败（${response.status}）`;
    throw new ApiError(code, message, response.status);
  }
  return payload.data as T;
}

async function requestList<T>(
  path: string,
): Promise<{ items: T[]; total: number }> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: authHeaders(),
  });
  const payload = (await response.json()) as PaginatedEnvelope<T>;
  if (!response.ok || !payload.success) {
    if (response.status === 401) handleUnauthorized(path);
    const code = payload.error?.code ?? "UNKNOWN";
    throw new ApiError(
      code,
      payload.error?.message ?? "请求失败",
      response.status,
    );
  }
  return {
    items: payload.data,
    total: payload.meta?.total ?? payload.data.length,
  };
}

/**
 * 登录。
 *
 * 同时存下 user：三个工作台要用真实 `user_id` 做提交人，而 JWT 的载荷前端不解 ——
 * 解 JWT 得引依赖且要处理签名，存后端给的 user 对象更直接。
 */
export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const token = await request<TokenResponse>(LOGIN_PATH, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setAccessToken(token.access_token);
  localStorage.setItem(USER_KEY, JSON.stringify(token.user));
  return token;
}

/** 待核验队列。 */
export function fetchVerificationQueue(page = 1, limit = 20) {
  return requestList<Episode>(
    `/verification/queue?page=${page}&limit=${limit}`,
  );
}

/** 待标注队列。 */
export function fetchAnnotationQueue(page = 1, limit = 20) {
  return requestList<Episode>(`/annotation/queue?page=${page}&limit=${limit}`);
}

/** 待审核队列。 */
export function fetchReviewQueue(page = 1, limit = 20) {
  return requestList<Episode>(
    `/annotation/review-queue?page=${page}&limit=${limit}`,
  );
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
    method: "POST",
    body: JSON.stringify(result),
  });
}

/** 提交标注。segments 是全量分段，不是增量补丁。 */
export function submitAnnotation(
  submission: AnnotationSubmit,
): Promise<Annotation> {
  return request<Annotation>(`/annotation/${submission.episode_id}`, {
    method: "POST",
    body: JSON.stringify(submission),
  });
}

/** 提交审核结果。 */
export function submitReview(result: ReviewResult): Promise<Episode> {
  return request<Episode>(`/annotation/${result.episode_id}/review`, {
    method: "POST",
    body: JSON.stringify(result),
  });
}
