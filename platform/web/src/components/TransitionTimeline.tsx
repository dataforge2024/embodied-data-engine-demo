/**
 * 状态流转轨迹。
 *
 * 回答两个问题：「谁在什么时候推的」与「卡在哪一步」。后者靠停留时长 ——
 * 某一环明显长于其他，那就是瓶颈。时长由相邻两条的时间差算出，不是存的字段。
 *
 * 人工与系统推进要能一眼分开（tasks.md 7.9）：图标 + 颜色 + 文案三重区分，
 * 只靠颜色的话色弱用户读不出来。
 */

import type { TransitionRecord } from "@contract";
import { formatFull, formatShort } from "../utils/datetime";
import { durationBetween } from "../utils/duration";
import { STATUS_LABELS } from "./EpisodeTable";
import "./TransitionTimeline.css";

interface Props {
  records: readonly TransitionRecord[];
  /** 加载中与空轨迹不是一回事，前者别显示「暂无记录」 */
  loading?: boolean;
}

/** 触发者的显示名。系统推进不冒充用户，所以环节名照原样出。 */
function actorLabel(record: TransitionRecord): string {
  const { actor } = record;
  if (actor.actor_type === "user") return actor.user_id ?? "未知用户";
  return actor.system_component ?? "系统";
}

export function TransitionTimeline({ records, loading = false }: Props) {
  if (loading) return <p className="timeline-empty">轨迹加载中…</p>;
  if (records.length === 0) {
    return <p className="timeline-empty">暂无流转记录</p>;
  }

  return (
    <ol className="transition-timeline">
      {records.map((record, index) => {
        // 停留时长 = 本条到下一条的间隔。最后一条还停在当前状态，算不出终点。
        const next = records[index + 1];
        const dwell = next
          ? durationBetween(record.occurred_at, next.occurred_at)
          : null;
        const isUser = record.actor.actor_type === "user";

        return (
          <li
            key={`${record.occurred_at}-${record.to_status}`}
            className={`timeline-row ${isUser ? "by-user" : "by-system"}`}
          >
            <span
              className="timeline-marker"
              title={isUser ? "人工推进" : "系统推进"}
              aria-label={isUser ? "人工推进" : "系统推进"}
            >
              {isUser ? "👤" : "⚙"}
            </span>

            <div className="timeline-body">
              <div className="timeline-head">
                <span className="timeline-transition">
                  {STATUS_LABELS[record.from_status]}
                  <span className="timeline-arrow" aria-hidden="true">
                    {" → "}
                  </span>
                  <strong>{STATUS_LABELS[record.to_status]}</strong>
                </span>
                <time
                  className="timeline-time"
                  dateTime={record.occurred_at}
                  title={formatFull(record.occurred_at)}
                >
                  {formatShort(record.occurred_at)}
                </time>
              </div>

              <div className="timeline-meta">
                <span className="timeline-actor">
                  {isUser ? "人工" : "系统"} · {actorLabel(record)}
                </span>
                {dwell && (
                  <span className="timeline-dwell" title="在该状态的停留时长">
                    停留 {dwell}
                  </span>
                )}
              </div>

              {record.reason && (
                <p className="timeline-reason">{record.reason}</p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
