/**
 * 浏览器控制台 WebSocket 客户端。
 *
 * 后端 `/api/v1/ws/console` 单向推送两类帧：Agent 上下线、Episode 上传进度。
 * 鉴权走 query 参数 —— 浏览器 `new WebSocket()` 带不了自定义 header。
 *
 * 断线后退避重连（1s → 最多 15s）。轮询仍然保留作为兜底：WS 只是让状态
 * 变化立刻可见，不是唯一数据来源。
 */

import type {
  ConsoleAgentStatusFrame,
  ConsoleUploadProgressFrame,
} from "@contract";

const WS_PATH = "/api/v1/ws/console";
const MAX_BACKOFF_MS = 15_000;

export interface ConsoleHandlers {
  onAgentStatus?: (frame: ConsoleAgentStatusFrame) => void;
  onUploadProgress?: (frame: ConsoleUploadProgressFrame) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

type Frame = ConsoleAgentStatusFrame | ConsoleUploadProgressFrame;

function socketUrl(token: string): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}${WS_PATH}?token=${encodeURIComponent(token)}`;
}

/**
 * 建立连接，返回断开函数。
 *
 * 4401/4403 是服务端的鉴权拒绝码，不重连 —— 重试也还是同一个 token。
 */
export function connectConsole(
  token: string,
  handlers: ConsoleHandlers,
): () => void {
  let socket: WebSocket | null = null;
  let timer: number | null = null;
  let backoff = 1_000;
  let closed = false;

  const open = () => {
    if (closed) return;
    socket = new WebSocket(socketUrl(token));

    socket.onopen = () => {
      backoff = 1_000;
      handlers.onOpen?.();
    };

    socket.onmessage = (event) => {
      let frame: Frame;
      try {
        frame = JSON.parse(event.data as string) as Frame;
      } catch {
        return; // 非 JSON 帧直接丢弃
      }
      if (frame.type === "console.agent_status") {
        handlers.onAgentStatus?.(frame as ConsoleAgentStatusFrame);
      } else if (frame.type === "console.upload_progress") {
        handlers.onUploadProgress?.(frame as ConsoleUploadProgressFrame);
      }
    };

    socket.onclose = (event) => {
      handlers.onClose?.();
      socket = null;
      if (closed || event.code === 4401 || event.code === 4403) return;
      timer = window.setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };
  };

  open();

  return () => {
    closed = true;
    if (timer !== null) window.clearTimeout(timer);
    socket?.close();
  };
}
