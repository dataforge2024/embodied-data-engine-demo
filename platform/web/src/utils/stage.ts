/**
 * 阶段分组：把 11 个 Episode 状态收成 6 个大阶段。
 *
 * 阶段是**展示层的分组**，不是契约概念 —— 状态机仍是 contract 里那 11 个状态，
 * 这里只决定「界面上归到哪一格」。改状态机要动 contract；改分组只动本文件。
 *
 * 六格严格交替（人 → 系统 → 人 → 系统 → 人 → 完成），所以看进度条就知道
 * 下一步是等人还是等系统。
 *
 * failed / rejected 不属于任何阶段：线性进度条表达不了「死在第 2 格」，
 * 所以单独做脱轨态，由 UI 换一种画法。
 */

import type { EpisodeStatus } from "@contract";

export type Stage =
  | "collect_manual"
  | "collect_auto"
  | "inspect_manual"
  | "annotate_auto"
  | "annotate_manual"
  | "done";

export const STAGE_ORDER: readonly Stage[] = [
  "collect_manual",
  "collect_auto",
  "inspect_manual",
  "annotate_auto",
  "annotate_manual",
  "done",
];

export const STAGE_LABELS: Record<Stage, string> = {
  collect_manual: "采集人工作业",
  collect_auto: "采集自动解析",
  inspect_manual: "采集人工质检",
  annotate_auto: "标注自动送标",
  annotate_manual: "标注人工作业",
  done: "完成",
};

/** 鼠标悬停时说明这一格在干什么，避免「质检」被误解成自动算子。 */
export const STAGE_HINTS: Record<Stage, string> = {
  collect_manual: "Agent 录制并分片上传",
  collect_auto: "算子流水线：预标注 / 质检 / 关键帧 / 异常",
  inspect_manual: "人工核验：看着自动质检报告判断可用性",
  annotate_auto: "送标处理：准备标注数据（将来接算子）",
  annotate_manual: "人工标注与标注审核",
  done: "已发布，可进训练集",
};

const STATUS_TO_STAGE: Record<EpisodeStatus, Stage | null> = {
  recording: "collect_manual",
  uploading: "collect_manual",
  uploaded: "collect_manual",
  processing: "collect_auto",
  verification_pending: "inspect_manual",
  annotation_processing: "annotate_auto",
  annotation_pending: "annotate_manual",
  annotation_review: "annotate_manual",
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

export function stageStates(status: EpisodeStatus): Record<Stage, StageState> {
  const current = stageOf(status);

  // 脱轨：卡在哪一格取决于死之前走到哪，但 Episode 只存当前状态，
  // 拿不到历史。保守起见把所有阶段标 blocked，由调用方单独渲染脱轨态。
  if (current === null) {
    return {
      collect_manual: "blocked",
      collect_auto: "blocked",
      inspect_manual: "blocked",
      annotate_auto: "blocked",
      annotate_manual: "blocked",
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
    collect_manual: 0,
    collect_auto: 0,
    inspect_manual: 0,
    annotate_auto: 0,
    annotate_manual: 0,
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
