/** 阶段进度条：采集 → 解析 → 质检 → 标注 → 完成。
 *
 * 大阶段给一眼看清「走到哪了」，子状态（processing / annotation_review 等）
 * 仍由状态 chip 显示 —— 两者互补，不是替代关系。
 */

import type { EpisodeStatus } from "@contract";
import {
  STAGE_HINTS,
  STAGE_LABELS,
  STAGE_ORDER,
  isDerailed,
  stageStates,
} from "../utils/stage";
import { STATUS_LABELS } from "./EpisodeTable";
import "./StageBar.css";

interface StageBarProps {
  status: EpisodeStatus;
  /** compact 用在表格单元格里，只画点不写字 */
  compact?: boolean;
}

export function StageBar({ status, compact = false }: StageBarProps) {
  const derailed = isDerailed(status);
  const states = stageStates(status);

  if (derailed) {
    // 线性进度条表达不了「死在第几格」—— Episode 只存当前状态，拿不到历史轨迹。
    // 所以脱轨态换一种画法，而不是画一条全灰的条。
    return (
      <div
        className={`stage-bar derailed ${compact ? "compact" : ""}`}
        title={`${STATUS_LABELS[status]} —— 流程中断`}
      >
        <span className="stage-derailed-chip">
          {status === "rejected" ? "已打回" : "失败"}
        </span>
      </div>
    );
  }

  return (
    <div className={`stage-bar ${compact ? "compact" : ""}`}>
      {STAGE_ORDER.map((stage, index) => (
        <div
          key={stage}
          className={`stage-step ${states[stage]}`}
          title={`${STAGE_LABELS[stage]} —— ${STAGE_HINTS[stage]}`}
        >
          {index > 0 && <span className="stage-connector" aria-hidden="true" />}
          <span className="stage-dot">
            {states[stage] === "done" ? "✓" : index + 1}
          </span>
          {!compact && (
            <span className="stage-label">{STAGE_LABELS[stage]}</span>
          )}
        </div>
      ))}
    </div>
  );
}
