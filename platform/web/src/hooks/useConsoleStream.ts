/**
 * 订阅控制台推送流，把帧折叠成页面直接能用的状态。
 *
 * `agentOnline` 只记录 WS 推来的增量。页面渲染时以 REST 拉到的列表为底，
 * 再用这里的覆盖 —— 避免刚连上时缺少历史节点。
 */

import { useEffect, useRef, useState } from "react";
import { connectConsole } from "../api/console-socket";

export interface UploadProgress {
  uploadedParts: number;
  totalParts: number;
  percent: number;
}

export interface ConsoleStream {
  /** agent_id → 是否在线；WS 推过的才有条目 */
  agentOnline: Record<string, boolean>;
  /** episode_id → 上传进度 */
  uploadProgress: Record<string, UploadProgress>;
  connected: boolean;
}

export function useConsoleStream(): ConsoleStream {
  const [agentOnline, setAgentOnline] = useState<Record<string, boolean>>({});
  const [uploadProgress, setUploadProgress] = useState<
    Record<string, UploadProgress>
  >({});
  const [connected, setConnected] = useState(false);
  const tokenRef = useRef(localStorage.getItem("rdh_access_token"));

  useEffect(() => {
    const token = tokenRef.current;
    if (!token) return;

    return connectConsole(token, {
      onOpen: () => setConnected(true),
      onClose: () => setConnected(false),
      onAgentStatus: (frame) =>
        setAgentOnline((prev) => ({ ...prev, [frame.agent_id]: frame.online })),
      onUploadProgress: (frame) =>
        setUploadProgress((prev) => ({
          ...prev,
          [frame.episode_id]: {
            uploadedParts: frame.uploaded_parts,
            totalParts: frame.total_parts,
            percent: frame.percent,
          },
        })),
    });
  }, []);

  return { agentOnline, uploadProgress, connected };
}
