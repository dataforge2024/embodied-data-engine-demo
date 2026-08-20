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
 * 所以单独做脱轨态，由 UI 换一种画法。传入流转轨迹时能定位中断位置
 * （`GET /episodes/{id}/transitions`），否则退回「全格 blocked」。
 */

import type { EpisodeStatus, TransitionRecord } from "@contract";

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

/**
 * 从轨迹里找出脱轨前停在哪一格。
 *
 * 轨迹按时间正序，最后一条的 `from_status` 就是死之前的状态 —— 它必然是个正常态
 * （failed / rejected 是终态，进去就出不来，不可能作为某次流转的源）。
 */
export function derailedAt(history: readonly TransitionRecord[]): Stage | null {
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const record = history[i];
    if (record === undefined) continue;
    const stage = stageOf(record.from_status);
    if (stage !== null) return stage;
  }
  return null;
}

export function stageStates(
  status: EpisodeStatus,
  history?: readonly TransitionRecord[],
): Record<Stage, StageState> {
  const current = stageOf(status);

  if (current === null) {
    // 脱轨：卡在哪一格取决于死之前走到哪。有轨迹就能定位 —— 中断格标 blocked，
    // 之前的格子仍算走过，之后的才是到不了。没轨迹（调用方没取）退回全 blocked。
    const brokeAt = history ? derailedAt(history) : null;
    if (brokeAt === null) {
      return {
        collect_manual: "blocked",
        collect_auto: "blocked",
        inspect_manual: "blocked",
        annotate_auto: "blocked",
        annotate_manual: "blocked",
        done: "blocked",
      };
    }
    const brokeIndex = STAGE_ORDER.indexOf(brokeAt);
    return Object.fromEntries(
      STAGE_ORDER.map((stage, index): [Stage, StageState] => [
        stage,
        index < brokeIndex ? "done" : "blocked",
      ]),
    ) as Record<Stage, StageState>;
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
