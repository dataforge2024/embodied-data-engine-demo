/**
 * 分段编辑状态。
 *
 * 所有变更走 segmentMath 的纯函数，返回新数组而非原地修改 ——
 * 这样撤销/重做只需保存历史快照。
 */

import { useCallback, useMemo, useState } from 'react';
import type { Segment } from '@contract';
import {
  mergeSegments,
  resizeSegment,
  splitSegment,
  validateSegments,
} from '../components/timeline/segmentMath';

export function useSegmentEditor(initial: readonly Segment[], durationMs: number) {
  const [history, setHistory] = useState<Segment[][]>([[...initial]]);
  const [cursor, setCursor] = useState(0);

  const segments = history[cursor] ?? [];

  const commit = useCallback(
    (next: Segment[]) => {
      setHistory((prev) => [...prev.slice(0, cursor + 1), next]);
      setCursor((prev) => prev + 1);
    },
    [cursor],
  );

  const split = useCallback(
    (segmentId: string, atMs: number) => {
      commit(splitSegment(segments, segmentId, atMs, crypto.randomUUID()));
    },
    [commit, segments],
  );

  const merge = useCallback(
    (firstId: string, secondId: string) => {
      commit(mergeSegments(segments, firstId, secondId));
    },
    [commit, segments],
  );

  const resize = useCallback(
    (segmentId: string, edge: 'start' | 'end', positionMs: number) => {
      commit(resizeSegment(segments, segmentId, edge, positionMs, durationMs));
    },
    [commit, durationMs, segments],
  );

  const relabel = useCallback(
    (segmentId: string, label: string, description?: string) => {
      commit(
        segments.map((segment) =>
          segment.segment_id === segmentId
            ? {
                ...segment,
                action_label: label,
                description: description ?? segment.description,
                // 人工修改后不再是算子产出
                source: null,
                confidence: null,
              }
            : segment,
        ),
      );
    },
    [commit, segments],
  );

  const remove = useCallback(
    (segmentId: string) => {
      commit(segments.filter((segment) => segment.segment_id !== segmentId));
    },
    [commit, segments],
  );

  const undo = useCallback(() => setCursor((prev) => Math.max(0, prev - 1)), []);
  const redo = useCallback(
    () => setCursor((prev) => Math.min(history.length - 1, prev + 1)),
    [history.length],
  );

  const validation = useMemo(() => validateSegments(segments, durationMs), [segments, durationMs]);

  return {
    segments,
    validation,
    canUndo: cursor > 0,
    canRedo: cursor < history.length - 1,
    split,
    merge,
    resize,
    relabel,
    remove,
    undo,
    redo,
  };
}
