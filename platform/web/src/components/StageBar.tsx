/** 阶段进度条：六格严格交替（人 → 系统 → 人 → 系统 → 人 → 完成）。
 *
 * 大阶段给一眼看清「走到哪了」，子状态（processing / annotation_review 等）
 * 仍由状态 chip 显示 —— 两者互补，不是替代关系。
 *
 * 脱轨态（failed / rejected）不落在任何格上。传 `history` 时能从轨迹里读出
 * 死之前停在哪一格，把中断位置标出来；不传就只能画一个笼统的脱轨 chip。
 *
 * compact（表格单元格）只显示当前所在阶段的名字，不画六格 —— 一行一条记录时
 * 六个点挤在一起反而看不清走到哪，一个名字更直接。详情走 title 提示。
 */

import type { EpisodeStatus, TransitionRecord } from "@contract";
import {
  STAGE_HINTS,
  STAGE_LABELS,
  STAGE_ORDER,
  derailedAt,
  isDerailed,
  stageOf,
  stageStates,
} from "../utils/stage";
import { STATUS_LABELS } from "./EpisodeTable";
import "./StageBar.css";

interface StageBarProps {
  status: EpisodeStatus;
  /** compact 用在表格单元格里，只显示当前阶段名字 */
  compact?: boolean;
  /** 流转轨迹。只有脱轨态用得上 —— 用来定位中断位置 */
  history?: readonly TransitionRecord[];
}

export function StageBar({ status, compact = false, history }: StageBarProps) {
  const derailed = isDerailed(status);
  const brokeAt = derailed && history ? derailedAt(history) : null;
  const derailLabel = status === "rejected" ? "已打回" : "失败";

  if (compact) {
    if (derailed) {
      const title = brokeAt
        ? `${derailLabel}于「${STAGE_LABELS[brokeAt]}」`
        : `${STATUS_LABELS[status]} —— 流程中断`;
      return (
        <span className="stage-chip derailed" title={title}>
          {derailLabel}
        </span>
      );
    }
    const current = stageOf(status);
    if (current === null) return <span className="stage-chip">—</span>;
    return (
      <span
        className="stage-chip current"
        title={`${STAGE_LABELS[current]} —— ${STAGE_HINTS[current]}`}
      >
        {STAGE_LABELS[current]}
      </span>
    );
  }

  const states = stageStates(status, history);

  // 拿不到轨迹时退回笼统的脱轨 chip：线性进度条画不出「死在第几格」，
  // 而全灰一条会让人误以为一步都没走。
  if (derailed && brokeAt === null) {
    return (
      <div
        className="stage-bar derailed"
        title={`${STATUS_LABELS[status]} —— 流程中断`}
      >
        <span className="stage-derailed-chip">{derailLabel}</span>
      </div>
    );
  }

  return (
    <div className={`stage-bar ${derailed ? "derailed-located" : ""}`}>
      {STAGE_ORDER.map((stage, index) => {
        const isBreakPoint = stage === brokeAt;
        const hint = isBreakPoint
          ? `${STAGE_LABELS[stage]} —— ${derailLabel}于此`
          : `${STAGE_LABELS[stage]} —— ${STAGE_HINTS[stage]}`;
        return (
          <div
            key={stage}
            className={`stage-step ${states[stage]} ${isBreakPoint ? "break-point" : ""}`}
            title={hint}
          >
            {index > 0 && (
              <span className="stage-connector" aria-hidden="true" />
            )}
            <span className="stage-dot">
              {isBreakPoint ? "✕" : states[stage] === "done" ? "✓" : ""}
            </span>
            <span className="stage-label">{STAGE_LABELS[stage]}</span>
          </div>
        );
      })}
    </div>
  );
}
