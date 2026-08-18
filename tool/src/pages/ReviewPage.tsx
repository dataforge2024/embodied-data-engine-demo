/**
 * 标注审核页面（交互④）。
 *
 * 注意：审核「退回」是让标注重做（回 annotation_pending），不是把 Episode 判死（rejected）。
 * 这两件事语义不同，UI 文案也要区分开。
 */

import { useEffect, useState } from 'react';
import type { Episode } from '@contract';
import { MultiViewPlayer } from '../components/player/MultiViewPlayer';
import { Timeline } from '../components/timeline/Timeline';
import { fetchReviewQueue, submitReview } from '../api/client';

export function ReviewPage({ reviewedBy }: { readonly reviewedBy: string }) {
  const [queue, setQueue] = useState<Episode[]>([]);
  const [current, setCurrent] = useState<Episode | null>(null);
  const [positionMs, setPositionMs] = useState(0);
  const [reason, setReason] = useState('');
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

  const decide = async (decision: 'approve' | 'reject') => {
    if (!current) return;
    if (decision === 'reject' && !reason.trim()) {
      setError('退回重做必须填写原因');
      return;
    }
    try {
      await submitReview({
        episode_id: current.episode_id,
        decision,
        reason: decision === 'reject' ? reason : null,
        reviewed_by: reviewedBy,
        reviewed_at: new Date().toISOString(),
      });
      setReason('');
      setError(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  if (error) return <p role="alert">{error}</p>;
  if (!current) return <p>审核队列为空</p>;

  return (
    <main className="review-page">
      <h1>标注审核 · 队列 {queue.length} 条</h1>
      <MultiViewPlayer
        streams={current.streams ?? []}
        durationMs={current.duration_ms ?? 0}
        onPositionChange={setPositionMs}
      />
      <Timeline
        segments={current.segments ?? []}
        durationMs={current.duration_ms ?? 0}
        positionMs={positionMs}
        onSeek={setPositionMs}
      />
      <div className="actions">
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="退回原因（退回时必填）"
        />
        <button type="button" onClick={() => decide('approve')}>
          通过并发布
        </button>
        <button type="button" onClick={() => decide('reject')}>
          退回重做
        </button>
      </div>
    </main>
  );
}
