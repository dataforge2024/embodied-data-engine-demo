/**
 * 时间轴与分段的纯计算逻辑。
 *
 * 抽成纯函数而不写在组件里：分段编辑的边界条件多（重叠、越界、最小时长），
 * 这些规则要与后端 `AnnotationSubmit` 的校验一致，独立出来才好测。
 */

import type { Segment } from '@contract';

/** 分段最小时长，与后端保持一致的编辑下限。 */
export const MIN_SEGMENT_MS = 100;

/** 时间轴缩放范围（像素/秒）。 */
export const MIN_PX_PER_SECOND = 10;
export const MAX_PX_PER_SECOND = 400;

/** 毫秒 → 像素。 */
export function msToPx(ms: number, pxPerSecond: number): number {
  return (ms / 1000) * pxPerSecond;
}

/** 像素 → 毫秒。 */
export function pxToMs(px: number, pxPerSecond: number): number {
  return (px / pxPerSecond) * 1000;
}

/** 缩放级别，夹在合理范围内。 */
export function clampZoom(pxPerSecond: number): number {
  return Math.min(MAX_PX_PER_SECOND, Math.max(MIN_PX_PER_SECOND, pxPerSecond));
}

/** 格式化时间轴刻度：`m:ss.mmm`。 */
export function formatTimestamp(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const millis = Math.floor(ms % 1000);
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
}

/** 按开始时间排序。 */
export function sortSegments(segments: readonly Segment[]): Segment[] {
  return [...segments].sort((a, b) => a.start_ms - b.start_ms);
}

/** 找出重叠的分段对。后端会拒绝重叠提交，前端提前拦住。 */
export function findOverlaps(segments: readonly Segment[]): Array<[Segment, Segment]> {
  const ordered = sortSegments(segments);
  const overlaps: Array<[Segment, Segment]> = [];
  for (let i = 1; i < ordered.length; i += 1) {
    const previous = ordered[i - 1];
    const current = ordered[i];
    if (previous && current && current.start_ms < previous.end_ms) {
      overlaps.push([previous, current]);
    }
  }
  return overlaps;
}

/** 分段集合是否可提交。 */
export function validateSegments(
  segments: readonly Segment[],
  durationMs: number,
): { valid: boolean; problems: string[] } {
  const problems: string[] = [];

  if (segments.length === 0) {
    problems.push('至少需要一个分段');
  }

  segments.forEach((segment) => {
    if (segment.end_ms <= segment.start_ms) {
      problems.push(`分段 ${segment.segment_id} 区间非法`);
    }
    if (segment.end_ms - segment.start_ms < MIN_SEGMENT_MS) {
      problems.push(`分段 ${segment.segment_id} 短于 ${MIN_SEGMENT_MS}ms`);
    }
    if (segment.start_ms < 0 || segment.end_ms > durationMs) {
      problems.push(`分段 ${segment.segment_id} 超出 Episode 时长`);
    }
    if (!segment.action_label) {
      problems.push(`分段 ${segment.segment_id} 缺少动作标签`);
    }
  });

  findOverlaps(segments).forEach(([a, b]) => {
    problems.push(`分段 ${a.segment_id} 与 ${b.segment_id} 重叠`);
  });

  return { valid: problems.length === 0, problems };
}

/**
 * 在指定位置切分一个分段，返回替换后的分段数组。
 *
 * 切分点太靠边（切出的片段小于 MIN_SEGMENT_MS）时原样返回 —— 静默拒绝比
 * 生成非法分段再报错更好用。
 */
export function splitSegment(
  segments: readonly Segment[],
  segmentId: string,
  atMs: number,
  newId: string,
): Segment[] {
  const target = segments.find((s) => s.segment_id === segmentId);
  if (!target) return [...segments];
  if (atMs - target.start_ms < MIN_SEGMENT_MS || target.end_ms - atMs < MIN_SEGMENT_MS) {
    return [...segments];
  }

  const left: Segment = { ...target, end_ms: atMs };
  const right: Segment = {
    ...target,
    segment_id: newId,
    start_ms: atMs,
    // 人工切分后不再是算子产出，清空来源与置信度
    source: null,
    confidence: null,
  };
  return sortSegments(segments.flatMap((s) => (s.segment_id === segmentId ? [left, right] : [s])));
}

/** 合并两个相邻分段。不相邻则原样返回。 */
export function mergeSegments(
  segments: readonly Segment[],
  firstId: string,
  secondId: string,
): Segment[] {
  const first = segments.find((s) => s.segment_id === firstId);
  const second = segments.find((s) => s.segment_id === secondId);
  if (!first || !second) return [...segments];

  const [earlier, later] = first.start_ms <= second.start_ms ? [first, second] : [second, first];
  const merged: Segment = {
    ...earlier,
    end_ms: later.end_ms,
    source: null,
    confidence: null,
  };
  return sortSegments([
    ...segments.filter((s) => s.segment_id !== firstId && s.segment_id !== secondId),
    merged,
  ]);
}

/** 拖拽调整分段边界，夹在相邻分段之间避免产生重叠。 */
export function resizeSegment(
  segments: readonly Segment[],
  segmentId: string,
  edge: 'start' | 'end',
  positionMs: number,
  durationMs: number,
): Segment[] {
  const ordered = sortSegments(segments);
  const index = ordered.findIndex((s) => s.segment_id === segmentId);
  if (index < 0) return ordered;

  const target = ordered[index];
  if (!target) return ordered;
  const previous = ordered[index - 1];
  const next = ordered[index + 1];

  if (edge === 'start') {
    const lowerBound = previous?.end_ms ?? 0;
    const upperBound = target.end_ms - MIN_SEGMENT_MS;
    const clamped = Math.min(Math.max(positionMs, lowerBound), upperBound);
    ordered[index] = { ...target, start_ms: clamped };
  } else {
    const lowerBound = target.start_ms + MIN_SEGMENT_MS;
    const upperBound = next?.start_ms ?? durationMs;
    const clamped = Math.max(Math.min(positionMs, upperBound), lowerBound);
    ordered[index] = { ...target, end_ms: clamped };
  }
  return ordered;
}

/** 分段总覆盖时长，用于展示标注完整度。 */
export function coverageMs(segments: readonly Segment[]): number {
  return sortSegments(segments).reduce((total, segment) => {
    return total + (segment.end_ms - segment.start_ms);
  }, 0);
}

/** 覆盖率（0~1）。 */
export function coverageRatio(segments: readonly Segment[], durationMs: number): number {
  if (durationMs <= 0) return 0;
  return Math.min(coverageMs(segments) / durationMs, 1);
}
