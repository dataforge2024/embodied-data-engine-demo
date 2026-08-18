/**
 * 时间轴：缩放、拖拽、分段展示。
 *
 * 编辑逻辑全在 segmentMath.ts（纯函数），本组件只负责渲染与交互事件。
 */

import { useMemo, useState } from 'react';
import type { Segment } from '@contract';
import {
  clampZoom,
  formatTimestamp,
  msToPx,
  pxToMs,
  sortSegments,
  validateSegments,
} from './segmentMath';

interface Props {
  readonly segments: readonly Segment[];
  readonly durationMs: number;
  readonly positionMs: number;
  readonly selectedId?: string | null;
  readonly onSeek?: (ms: number) => void;
  readonly onSelect?: (segmentId: string) => void;
  readonly onResize?: (segmentId: string, edge: 'start' | 'end', positionMs: number) => void;
}

const DEFAULT_PX_PER_SECOND = 60;
const TICK_INTERVAL_MS = 1000;

export function Timeline({
  segments,
  durationMs,
  positionMs,
  selectedId,
  onSeek,
  onSelect,
  onResize,
}: Props) {
  const [pxPerSecond, setPxPerSecond] = useState(DEFAULT_PX_PER_SECOND);
  const ordered = useMemo(() => sortSegments(segments), [segments]);
  const validation = useMemo(() => validateSegments(segments, durationMs), [segments, durationMs]);

  const trackWidth = msToPx(durationMs, pxPerSecond);
  const tickCount = Math.floor(durationMs / TICK_INTERVAL_MS) + 1;

  const handleTrackClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    onSeek?.(pxToMs(event.clientX - bounds.left, pxPerSecond));
  };

  return (
    <section className="timeline">
      <header className="timeline-toolbar">
        <label>
          缩放
          <input
            type="range"
            min={10}
            max={400}
            value={pxPerSecond}
            onChange={(event) => setPxPerSecond(clampZoom(Number(event.target.value)))}
          />
        </label>
        <span>{ordered.length} 个分段</span>
        {!validation.valid && (
          <span className="timeline-problems" role="alert">
            {validation.problems.length} 项问题待修正
          </span>
        )}
      </header>

      <div className="timeline-scroll">
        <div className="timeline-track" style={{ width: trackWidth }} onClick={handleTrackClick}>
          <div className="timeline-ruler">
            {Array.from({ length: tickCount }, (_, index) => (
              <span
                key={index}
                className="tick"
                style={{ left: msToPx(index * TICK_INTERVAL_MS, pxPerSecond) }}
              >
                {formatTimestamp(index * TICK_INTERVAL_MS)}
              </span>
            ))}
          </div>

          {ordered.map((segment) => (
            <div
              key={segment.segment_id}
              className={`segment-bar${segment.segment_id === selectedId ? ' selected' : ''}${
                segment.source ? ' from-algo' : ''
              }`}
              style={{
                left: msToPx(segment.start_ms, pxPerSecond),
                width: msToPx(segment.end_ms - segment.start_ms, pxPerSecond),
              }}
              onClick={(event) => {
                event.stopPropagation();
                onSelect?.(segment.segment_id);
              }}
              title={
                segment.source
                  ? `${segment.action_label ?? '未标注'}（${segment.source} 预标注）`
                  : (segment.action_label ?? '未标注')
              }
            >
              <span
                className="handle start"
                onMouseDown={(event) => {
                  event.stopPropagation();
                  onResize?.(segment.segment_id, 'start', segment.start_ms);
                }}
              />
              <span className="label">{segment.action_label ?? '未标注'}</span>
              <span
                className="handle end"
                onMouseDown={(event) => {
                  event.stopPropagation();
                  onResize?.(segment.segment_id, 'end', segment.end_ms);
                }}
              />
            </div>
          ))}

          <div className="playhead" style={{ left: msToPx(positionMs, pxPerSecond) }} />
        </div>
      </div>
    </section>
  );
}
