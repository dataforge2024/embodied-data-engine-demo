/**
 * 阶段分组：把 10 个 Episode 状态收成 5 个大阶段。
 *
 * 阶段是**展示层的分组**，不是契约概念 —— 状态机仍是 contract 里那 10 个状态，
 * 这里只决定「界面上归到哪一格」。改状态机要动 contract；改分组只动本文件。
 *
 * failed / rejected 不属于任何阶段：线性进度条表达不了「死在第 2 格」，
 * 所以单独做脱轨态，由 UI 换一种画法。
 */

import type { EpisodeStatus } from "@contract";

export type Stage = "collect" | "parse" | "inspect" | "annotate" | "done";

export const STAGE_ORDER: readonly Stage[] = [
  "collect",
  "parse",
  "inspect",
  "annotate",
  "done",
];

export const STAGE_LABELS: Record<Stage, string> = {
  collect: "采集",
  parse: "解析",
  inspect: "质检",
  annotate: "标注",
  done: "完成",
};

/** 鼠标悬停时说明这一格在干什么，避免「质检」被误解成自动算子。 */
export const STAGE_HINTS: Record<Stage, string> = {
  collect: "Agent 录制并分片上传",
  parse: "算子流水线：预标注 / 质检 / 关键帧 / 异常",
  inspect: "人工核验：看着自动质检报告判断可用性",
  annotate: "人工标注与标注审核",
  done: "已发布，可进训练集",
};

const STATUS_TO_STAGE: Record<EpisodeStatus, Stage | null> = {
  recording: "collect",
  uploading: "collect",
  uploaded: "collect",
  processing: "parse",
  verification_pending: "inspect",
  annotation_pending: "annotate",
  annotation_review: "annotate",
  published: "done",
  // 脱轨态：不落在任何阶段上
  rejected: null,
  failed: null,
};

/** 状态所属阶段；脱轨态返回 null。 */
export function stageOf(status: EpisodeStatus): Stage | null {
  return STATUS_TO_STAGE[status] ?? null;
}

export function isDerailed(status: EpisodeStatus): boolean {
  return stageOf(status) === null;
}

/**
 * 阶段的推进程度。
 *
 * - `done`：已走过
 * - `current`：正停在这一格
 * - `pending`：还没到
 * - `blocked`：脱轨，后续阶段都到不了了
 */
export type StageState = "done" | "current" | "pending" | "blocked";

export function stageStates(
  status: EpisodeStatus,
): Record<Stage, StageState> {
  const current = stageOf(status);

  // 脱轨：卡在哪一格取决于死之前走到哪，但 Episode 只存当前状态，
  // 拿不到历史。保守起见把所有阶段标 blocked，由调用方单独渲染脱轨态。
  if (current === null) {
    return {
      collect: "blocked",
      parse: "blocked",
      inspect: "blocked",
      annotate: "blocked",
      done: "blocked",
    };
  }

  const currentIndex = STAGE_ORDER.indexOf(current);
  const entries = STAGE_ORDER.map((stage, index): [Stage, StageState] => {
    if (index < currentIndex) return [stage, "done"];
    if (index === currentIndex) return [stage, "current"];
    return [stage, "pending"];
  });
  return Object.fromEntries(entries) as Record<Stage, StageState>;
}

/** 按阶段汇总一批 Episode 的数量，附带脱轨计数。 */
export function countByStage(
  statuses: readonly EpisodeStatus[],
): Record<Stage, number> & { derailed: number } {
  const counts = {
    collect: 0,
    parse: 0,
    inspect: 0,
    annotate: 0,
    done: 0,
    derailed: 0,
  };
  for (const status of statuses) {
    const stage = stageOf(status);
    if (stage === null) counts.derailed += 1;
    else counts[stage] += 1;
  }
  return counts;
}
