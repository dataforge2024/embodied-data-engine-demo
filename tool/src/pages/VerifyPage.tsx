/**
 * 核验页面（交互④）。
 *
 * 核验只判断「数据本身能不能用」，不做标注。打回必须填原因 —— 后端也会校验。
 */

import { useEffect, useState } from "react";
import type { Episode } from "@contract";
import { MultiViewPlayer } from "../components/player/MultiViewPlayer";
import {
  fetchEpisode,
  fetchVerificationQueue,
  submitVerification,
} from "../api/client";
import "./workspace.css";

export function VerifyPage({
  verifiedBy,
  episodeId,
}: {
  readonly verifiedBy: string;
  readonly episodeId?: string;
}) {
  const [queue, setQueue] = useState<Episode[]>([]);
  const [current, setCurrent] = useState<Episode | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    fetchVerificationQueue()
      .then(({ items }) => {
        setQueue(items);
        setCurrent(items[0] ?? null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(() => {
    if (episodeId) {
      fetchEpisode(episodeId)
        .then((ep) => {
          setCurrent(ep);
          setQueue([]);
        })
        .catch((e: Error) => setError(e.message));
    } else {
      reload();
    }
  }, [episodeId]);

  const decide = async (decision: "approve" | "reject") => {
    if (!current) return;
    if (decision === "reject" && !reason.trim()) {
      setError("打回必须填写原因");
      return;
    }
    try {
      await submitVerification({
        episode_id: current.episode_id,
        decision,
        reason: decision === "reject" ? reason : null,
        checked_topics: (current.streams ?? []).map((s) => s.topic),
        verified_by: verifiedBy,
        verified_at: new Date().toISOString(),
      });
      setReason("");
      setError(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error) return <p role="alert">{error}</p>;
  if (!current) return <p>核验队列为空</p>;

  return (
    <main className="verify-page">
      <h1>核验 · 队列 {queue.length} 条</h1>
      <MultiViewPlayer
        streams={current.streams ?? []}
        durationMs={current.duration_ms ?? 0}
      />
      {current.quality && (
        <aside className="quality-report">
          <h2>质检结果</h2>
          <p className={current.quality.passed ? "passed" : "failed"}>
            {current.quality.passed ? "通过" : "未通过"}
          </p>
          <ul>
            {(current.quality.issues ?? []).map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </aside>
      )}
      <div className="actions">
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="打回原因（打回时必填）"
        />
        <button
          type="button"
          className="primary"
          onClick={() => decide("approve")}
        >
          通过
        </button>
        <button
          type="button"
          className="danger"
          onClick={() => decide("reject")}
        >
          打回
        </button>
      </div>
    </main>
  );
}
