/**
 * 标注审核页面（交互④）。
 *
 * 注意：审核「退回」是让标注重做（回 annotation_pending），不是把 Episode 判死（rejected）。
 * 这两件事语义不同，UI 文案也要区分开。
 *
 * 分段与备注读 Annotation 而不是 Episode：审核要看的是**标注人提交的那一版**，
 * 而 Annotation 还带着 revision 与上一次退回原因 —— Episode 上只有当前分段。
 */

import { useEffect, useState } from "react";
import type { Annotation, Episode } from "@contract";
import { MultiViewPlayer } from "../components/player/MultiViewPlayer";
import { Timeline } from "../components/timeline/Timeline";
import { fetchAnnotation, fetchReviewQueue, submitReview } from "../api/client";
import { formatTimestamp } from "../components/timeline/segmentMath";

export function ReviewPage({ reviewedBy }: { readonly reviewedBy: string }) {
  const [queue, setQueue] = useState<Episode[]>([]);
  const [current, setCurrent] = useState<Episode | null>(null);
  const [annotation, setAnnotation] = useState<Annotation | null>(null);
  const [positionMs, setPositionMs] = useState(0);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reload = () => {
    fetchReviewQueue()
      .then(({ items }) => {
        setQueue(items);
        setCurrent(items[0] ?? null);
      })
      .catch((e: Error) => setError(e.message));
  };

  useEffect(reload, []);

  // 队列换条时拉对应的标注记录
  useEffect(() => {
    if (!current) {
      setAnnotation(null);
      return;
    }
    fetchAnnotation(current.episode_id)
      .then(setAnnotation)
      // 标注记录拉不到不该挡住回放与裁决 —— 降级为「看不到标注内容」
      .catch(() => setAnnotation(null));
  }, [current]);

  const decide = async (decision: "approve" | "reject") => {
    if (!current) return;
    if (decision === "reject" && !reason.trim()) {
      setError("退回重做必须填写原因");
      return;
    }
    try {
      await submitReview({
        episode_id: current.episode_id,
        decision,
        reason: decision === "reject" ? reason : null,
        reviewed_by: reviewedBy,
        reviewed_at: new Date().toISOString(),
      });
      setReason("");
      setError(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error && !current) return <p role="alert">{error}</p>;
  if (!current) return <p>审核队列为空</p>;

  const segments = annotation?.segments ?? current.segments ?? [];

  return (
    <main className="review-page">
      <h1>标注审核 · 队列 {queue.length} 条</h1>
      <MultiViewPlayer
        streams={current.streams ?? []}
        durationMs={current.duration_ms ?? 0}
        onPositionChange={setPositionMs}
      />
      <Timeline
        segments={segments}
        durationMs={current.duration_ms ?? 0}
        positionMs={positionMs}
        onSeek={setPositionMs}
      />

      <section className="annotation-review">
        <h2>
          标注内容
          {annotation?.revision != null && annotation.revision > 1 && (
            <span className="revision">第 {annotation.revision} 版</span>
          )}
        </h2>
        {annotation?.annotated_by && (
          <p className="annotated-by">标注人：{annotation.annotated_by}</p>
        )}

        {segments.length === 0 ? (
          <p>无分段</p>
        ) : (
          <ol className="segment-list">
            {segments.map((segment) => (
              <li key={segment.segment_id}>
                <button type="button" onClick={() => setPositionMs(segment.start_ms)}>
                  {formatTimestamp(segment.start_ms)} –{" "}
                  {formatTimestamp(segment.end_ms)}
                </button>
                <strong>{segment.action_label ?? "（无标签）"}</strong>
                {segment.description && <p>{segment.description}</p>}
                <span className="source">
                  {segment.source ? `算子：${segment.source}` : "人工"}
                </span>
              </li>
            ))}
          </ol>
        )}

        <h3>标注备注</h3>
        <p className="notes">{annotation?.notes || "（未填写）"}</p>
      </section>

      <div className="actions">
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="退回原因（退回时必填）"
        />
        <button type="button" onClick={() => decide("approve")}>
          通过并发布
        </button>
        <button type="button" onClick={() => decide("reject")}>
          退回重做
        </button>
      </div>

      {error && <p role="alert">{error}</p>}
    </main>
  );
}
