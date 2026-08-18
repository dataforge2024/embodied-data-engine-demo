/**
 * 多视角同步回放。
 *
 * 各路相机流共用一个 SyncController 主时钟（见 SyncController.ts），
 * 而不是让某一路 video 当基准 —— 否则该路卡顿会把其余路带偏。
 */

import { useEffect, useRef, useState } from 'react';
import type { SensorStream } from '@contract';
import { SyncController, type SyncState } from './SyncController';
import { formatTimestamp } from '../timeline/segmentMath';

interface Props {
  readonly streams: readonly SensorStream[];
  readonly durationMs: number;
  readonly onPositionChange?: (positionMs: number) => void;
}

export function MultiViewPlayer({ streams, durationMs, onPositionChange }: Props) {
  const controllerRef = useRef<SyncController | null>(null);
  const [state, setState] = useState<SyncState>({ positionMs: 0, playing: false, rate: 1 });

  const cameras = streams.filter((s) => s.kind === 'camera');

  useEffect(() => {
    const controller = new SyncController();
    controllerRef.current = controller;
    const unsubscribe = controller.subscribe((next) => {
      setState(next);
      onPositionChange?.(next.positionMs);
    });
    // 必须 dispose，否则 rAF 循环泄漏
    return () => {
      unsubscribe();
      controller.dispose();
      controllerRef.current = null;
    };
  }, [onPositionChange]);

  const registerVideo = (stream: SensorStream) => (element: HTMLVideoElement | null) => {
    if (element && controllerRef.current) {
      controllerRef.current.attach({
        topic: stream.topic,
        startOffsetMs: stream.start_offset_ms ?? 0,
        element,
      });
    }
  };

  const controller = controllerRef.current;

  return (
    <section className="multi-view-player">
      <div className="viewport-grid">
        {cameras.map((stream) => (
          <figure key={stream.topic}>
            <video
              ref={registerVideo(stream)}
              src={stream.preview_url ?? undefined}
              muted
              playsInline
              preload="metadata"
            />
            <figcaption>
              {stream.topic}
              {stream.frequency_hz ? ` · ${stream.frequency_hz}Hz` : ''}
            </figcaption>
          </figure>
        ))}
        {cameras.length === 0 && <p className="empty">该 Episode 无相机流</p>}
      </div>

      <div className="transport">
        <button
          type="button"
          onClick={() => (state.playing ? controller?.pause() : controller?.play())}
        >
          {state.playing ? '暂停' : '播放'}
        </button>
        <input
          type="range"
          min={0}
          max={durationMs}
          value={state.positionMs}
          onChange={(event) => controller?.seek(Number(event.target.value))}
          aria-label="播放进度"
        />
        <output>
          {formatTimestamp(state.positionMs)} / {formatTimestamp(durationMs)}
        </output>
        <select
          value={state.rate}
          onChange={(event) => controller?.setRate(Number(event.target.value))}
          aria-label="播放倍速"
        >
          {[0.25, 0.5, 1, 2, 4].map((rate) => (
            <option key={rate} value={rate}>
              {rate}×
            </option>
          ))}
        </select>
      </div>
    </section>
  );
}
