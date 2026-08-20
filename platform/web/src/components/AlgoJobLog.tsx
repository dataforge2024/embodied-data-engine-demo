/**
 * 算子运行日志。
 *
 * 回答「采集自动解析这一步到底跑了什么」—— 与 TransitionTimeline 互补：
 * 那个组件答「卡在哪个状态」，这个答「自动环节内部跑了哪几个算子、
 * 成不成功、耗时多久」。
 *
 * 4 个算子并发跑（design.md），谁先谁后不重要，按开始时间排列只是为了稳定展示。
 */

import type { AlgoJobRunRecord } from "@contract";
import { formatFull, formatShort } from "../utils/datetime";
import { formatDuration } from "../utils/duration";
import "./AlgoJobLog.css";

interface Props {
  records: readonly AlgoJobRunRecord[];
  /** 加载中与「还没跑」不是一回事，前者别显示「暂无记录」 */
  loading?: boolean;
}

const OPERATOR_LABELS: Record<AlgoJobRunRecord["operator"], string> = {
  preannotate: "预标注",
  quality: "质检",
  keyframe: "关键帧",
  anomaly: "异常检测",
};

const STATUS_LABELS: Record<AlgoJobRunRecord["status"], string> = {
  pending: "排队中",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  timeout: "超时",
};

export function AlgoJobLog({ records, loading = false }: Props) {
  if (loading) return <p className="algo-log-empty">日志加载中…</p>;
  if (records.length === 0) {
    return <p className="algo-log-empty">暂无算子运行记录</p>;
  }

  return (
    <ul className="algo-job-log">
      {records.map((record) => {
        const ok = record.status === "succeeded";
        return (
          <li key={record.job_id} className={`algo-job-row ${ok ? "ok" : "bad"}`}>
            <span className={`algo-job-status ${record.status}`}>
              {STATUS_LABELS[record.status]}
            </span>
            <span className="algo-job-operator">
              {OPERATOR_LABELS[record.operator]}
            </span>
            <span className="algo-job-version" title="模型版本（镜像 tag）">
              {record.model_version}
            </span>
            <span
              className="algo-job-duration"
              title={`${formatFull(record.started_at)} – ${formatFull(record.finished_at)}`}
            >
              耗时{" "}
              {formatDuration(
                new Date(record.finished_at).getTime() -
                  new Date(record.started_at).getTime(),
              )}
            </span>
            <time
              className="algo-job-time"
              dateTime={record.started_at}
              title={formatFull(record.started_at)}
            >
              {formatShort(record.started_at)}
            </time>
            {record.error_message && (
              <p className="algo-job-error">{record.error_message}</p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
