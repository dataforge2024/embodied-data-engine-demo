/** Episode 表格。任务详情与采集记录页共用。
 *
 * 「所属任务」列在任务详情里是冗余的（整张表都属于同一个任务），所以做成可关。
 *
 * 每行可展开看状态流转轨迹。轨迹按需拉取而不随列表一起取 —— 一页 20 条各带
 * 10 条流转就是 200 条记录，绝大多数没人会看。
 */

import type { AlgoJobRunRecord, Episode, EpisodeStatus, User } from "@contract";
import { isTerminal } from "@contract";
import { Fragment, useCallback, useState } from "react";
import { fetchAlgoJobs, fetchTransitions } from "../api/client";
import type { TransitionRecord } from "@contract";
import type { UploadProgress } from "../hooks/useConsoleStream";
import { formatFull, formatShort } from "../utils/datetime";
import { TOOL_STAGE_LABELS, toolLink, toolStageOf } from "../utils/toolLink";
import { AlgoJobLog } from "./AlgoJobLog";
import { StageBar } from "./StageBar";
import { TransitionTimeline } from "./TransitionTimeline";

export const STATUS_LABELS: Record<EpisodeStatus, string> = {
  recording: "录制中",
  uploading: "上传中",
  uploaded: "已上传",
  processing: "解析中",
  verification_pending: "待核验",
  annotation_processing: "送标处理中",
  annotation_pending: "待标注",
  annotation_review: "标注审核",
  published: "成功",
  rejected: "已打回",
  failed: "失败",
};

export const STATUS_COLORS: Record<EpisodeStatus, string> = {
  recording: "#38bdf8",
  uploading: "#22d3ee",
  uploaded: "#4fd1c5",
  processing: "#f59e0b",
  verification_pending: "#f97316",
  annotation_processing: "#a855f7",
  annotation_pending: "#c084fc",
  annotation_review: "#fb923c",
  published: "#10b981",
  rejected: "#ef4444",
  failed: "#64748b",
};

interface EpisodeTableProps {
  episodes: Episode[];
  /** user_id → 用户，用于把 recorded_by 显示成人名 */
  users: Record<string, User>;
  /** episode_id → 实时上传进度 */
  uploadProgress: Record<string, UploadProgress>;
  /** 任务名映射；不传则「所属任务」显示 ID 前 8 位 */
  taskNames?: Record<string, string>;
  /** 任务详情页里关掉，避免整列同一个值 */
  showTaskColumn?: boolean;
  emptyText?: string;
}

/**
 * 展开中的那一条轨迹 + 算子日志。
 *
 * 同时只展开一条：详情多条同时展开会把表格撑得没法看，也省掉按 id 缓存多份
 * 记录的状态。两份数据并发拉取 —— 互不依赖，没必要串行等。
 */
function useTransitionHistory() {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [records, setRecords] = useState<readonly TransitionRecord[]>([]);
  const [algoJobs, setAlgoJobs] = useState<readonly AlgoJobRunRecord[]>([]);
  const [loading, setLoading] = useState(false);

  const toggle = useCallback(
    (episodeId: string) => {
      if (expandedId === episodeId) {
        setExpandedId(null);
        setRecords([]);
        setAlgoJobs([]);
        return;
      }
      setExpandedId(episodeId);
      setRecords([]);
      setAlgoJobs([]);
      setLoading(true);
      Promise.all([fetchTransitions(episodeId), fetchAlgoJobs(episodeId)])
        .then(([history, jobs]) => {
          // 拉取期间用户可能又点了别的行，回来的结果就不该覆盖
          setExpandedId((current) => {
            if (current === episodeId) {
              setRecords(history);
              setAlgoJobs(jobs);
            }
            return current;
          });
        })
        .catch(() => {
          setRecords([]);
          setAlgoJobs([]);
        })
        .finally(() => setLoading(false));
    },
    [expandedId],
  );

  return { expandedId, toggle, records, algoJobs, loading };
}

/**
 * 人工环节入口。
 *
 * 只有在等人操作的三个状态才给链接 —— 其余状态要么在自动流水线里跑着，
 * 要么已到终态，跳过去没有可做的事。
 */
function ToolEntry({
  episodeId,
  status,
}: {
  episodeId: string;
  status: EpisodeStatus;
}) {
  const stage = toolStageOf(status);
  if (stage === null) return <span className="tool-idle">—</span>;
  return (
    <a
      className="tool-link"
      href={toolLink(episodeId, stage)}
      target="_blank"
      rel="noreferrer"
      title="在 Tool 工作台打开（新标签页）"
    >
      {TOOL_STAGE_LABELS[stage]}
    </a>
  );
}

export function EpisodeTable({
  episodes,
  users,
  uploadProgress,
  taskNames,
  showTaskColumn = true,
  emptyText = "暂无记录",
}: EpisodeTableProps) {
  // 展开列 + 阶段列 + 人工环节入口列
  const columnCount = showTaskColumn ? 14 : 13;
  const { expandedId, toggle, records, algoJobs, loading } =
    useTransitionHistory();

  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th aria-label="展开轨迹" />
            <th>Episode ID</th>
            <th>阶段</th>
            <th>子状态</th>
            <th>上传进度</th>
            <th>采集员</th>
            <th>Agent</th>
            {showTaskColumn && <th>所属任务</th>}
            <th>时长</th>
            <th>大小</th>
            <th>机型</th>
            <th>场景</th>
            <th>创建时间</th>
            <th>人工环节</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((episode) => {
            const status = episode.status as EpisodeStatus;
            const progress = uploadProgress[episode.episode_id];
            const recorder = episode.recorded_by
              ? (users[episode.recorded_by]?.display_name ??
                episode.recorded_by.slice(0, 8))
              : "—";
            const expanded = expandedId === episode.episode_id;
            return (
              <Fragment key={episode.episode_id}>
                <tr className={isTerminal(status) ? "terminal-row" : ""}>
                  <td className="expand-cell">
                    <button
                      type="button"
                      className="expand-button"
                      aria-expanded={expanded}
                      title={expanded ? "收起流转轨迹" : "展开流转轨迹"}
                      onClick={() => toggle(episode.episode_id)}
                    >
                      {expanded ? "▾" : "▸"}
                    </button>
                  </td>
                  <td className="mono-cell" title={episode.episode_id}>
                    {episode.episode_id.slice(0, 8)}
                  </td>
                  <td>
                    {/* 脱轨态传轨迹才能标出中断位置；未展开时拿不到，退回全格 blocked */}
                    <StageBar
                      status={status}
                      compact
                      history={expanded ? records : undefined}
                    />
                  </td>
                  <td>
                    <span
                      className="status-chip"
                      style={{ backgroundColor: STATUS_COLORS[status] }}
                    >
                      {STATUS_LABELS[status] || episode.status}
                    </span>
                  </td>
                  <td className="progress-cell">
                    {progress ? (
                      <div
                        className="progress-bar-container"
                        title={`${progress.uploadedParts} / ${progress.totalParts} 分片`}
                      >
                        <div
                          className="progress-bar"
                          style={{ width: `${progress.percent}%` }}
                        />
                        <span className="progress-text">
                          {Math.round(progress.percent)}%
                        </span>
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{recorder}</td>
                  <td className="mono-cell">{episode.agent_id}</td>
                  {showTaskColumn && (
                    <td className="task-link" title={episode.task_id}>
                      {taskNames?.[episode.task_id] ??
                        episode.task_id.slice(0, 8)}
                    </td>
                  )}
                  <td className="mono-cell">
                    {episode.duration_ms
                      ? `${(episode.duration_ms / 1000).toFixed(1)}s`
                      : "—"}
                  </td>
                  <td className="mono-cell">
                    {episode.size_bytes
                      ? `${(episode.size_bytes / 1024 / 1024).toFixed(1)} MB`
                      : "—"}
                  </td>
                  <td className="mono-cell">{episode.robot_model || "—"}</td>
                  <td>{episode.scene || "—"}</td>
                  <td
                    className="mono-cell time-cell"
                    title={formatFull(episode.created_at)}
                  >
                    {formatShort(episode.created_at)}
                  </td>
                  <td className="tool-cell">
                    <ToolEntry episodeId={episode.episode_id} status={status} />
                  </td>
                </tr>
                {expanded && (
                  <tr className="timeline-row-wrapper">
                    <td colSpan={columnCount}>
                      <div className="expanded-detail">
                        <section className="expanded-section">
                          <h4 className="expanded-section-title">
                            状态流转轨迹
                          </h4>
                          <TransitionTimeline
                            records={records}
                            loading={loading}
                          />
                        </section>
                        <section className="expanded-section">
                          <h4 className="expanded-section-title">
                            算子运行日志
                          </h4>
                          <AlgoJobLog records={algoJobs} loading={loading} />
                        </section>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          {episodes.length === 0 && (
            <tr className="empty-row">
              <td colSpan={columnCount}>{emptyText}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
