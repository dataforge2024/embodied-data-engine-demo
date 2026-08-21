/**
 * 自动环节运行日志。
 *
 * 按**阶段**讲事情，不按算子平铺：解析是一段（内部并发跑 4 个算子），送标是另一段。
 * 用户要看的是「解析开始 → 解析完成」这条线，算子明细是展开后的细节。
 *
 * 与 TransitionTimeline 互补：那个组件答「卡在哪个状态」，这个答「自动环节跑了多久」。
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
  annotation_processing: "送标处理",
};

/** 一个自动阶段：由它包含的算子记录归并而来。 */
interface Phase {
  key: string;
  label: string;
  /** 非空 —— toPhases 只在有记录时建阶段 */
  records: readonly [AlgoJobRunRecord, ...AlgoJobRunRecord[]];
}

/**
 * 把记录归到阶段。
 *
 * 送标是独立阶段，其余四个算子都属于解析 —— 它们并发跑，没有先后可言，
 * 所以合成一段而不是四行。
 */
function toPhases(records: readonly AlgoJobRunRecord[]): Phase[] {
  const parse = records.filter((r) => r.operator !== "annotation_processing");
  const submit = records.filter((r) => r.operator === "annotation_processing");

  const phases: Phase[] = [];
  const [firstParse, ...restParse] = parse;
  if (firstParse) {
    phases.push({ key: "parse", label: "解析", records: [firstParse, ...restParse] });
  }
  const [firstSubmit, ...restSubmit] = submit;
  if (firstSubmit) {
    phases.push({ key: "submit", label: "送标", records: [firstSubmit, ...restSubmit] });
  }
  return phases;
}

const time = (v: string) => new Date(v).getTime();

export function AlgoJobLog({ records, loading = false }: Props) {
  if (loading) return <p className="algo-log-empty">日志加载中…</p>;
  if (records.length === 0) {
    return <p className="algo-log-empty">暂无自动环节记录</p>;
  }

  return (
    <ol className="algo-phase-log">
      {toPhases(records).map((phase) => {
        // 阶段起止 = 内部记录的最早开始与最晚结束（算子并发，不能取首尾两条）
        const startedAt = phase.records.reduce(
          (min, r) => (time(r.started_at) < time(min) ? r.started_at : min),
          phase.records[0].started_at,
        );
        const finishedAt = phase.records.reduce(
          (max, r) => (time(r.finished_at) > time(max) ? r.finished_at : max),
          phase.records[0].finished_at,
        );
        const failed = phase.records.filter((r) => r.status !== "succeeded");
        const ok = failed.length === 0;

        return (
          <li
            key={phase.key}
            className={`algo-phase ${ok ? "ok" : "bad"}`}
          >
            <div className="algo-phase-line">
              <span className="algo-phase-dot" aria-hidden="true" />
              <span className="algo-phase-label">{phase.label}开始</span>
              <time
                className="algo-phase-time"
                dateTime={startedAt}
                title={formatFull(startedAt)}
              >
                {formatShort(startedAt)}
              </time>
            </div>

            <div className="algo-phase-line">
              <span
                className={`algo-phase-dot ${ok ? "done" : "fail"}`}
                aria-hidden="true"
              />
              <span className="algo-phase-label">
                {phase.label}
                {ok ? "完成" : "失败"}
              </span>
              <span className="algo-phase-duration">
                耗时 {formatDuration(time(finishedAt) - time(startedAt))}
              </span>
              <time
                className="algo-phase-time"
                dateTime={finishedAt}
                title={formatFull(finishedAt)}
              >
                {formatShort(finishedAt)}
              </time>
            </div>

            {/* 算子明细收进来：想看细节的人展开，默认不占版面 */}
            <details className="algo-phase-detail">
              <summary>
                {phase.records.length} 个算子
                {failed.length > 0 && ` · ${failed.length} 个失败`}
              </summary>
              <ul className="algo-job-list">
                {phase.records.map((record) => (
                  <li
                    key={record.job_id}
                    className={`algo-job-item ${record.status === "succeeded" ? "ok" : "bad"}`}
                  >
                    <span className="algo-job-operator">
                      {OPERATOR_LABELS[record.operator]}
                    </span>
                    <span className="algo-job-version" title="模型版本（镜像 tag）">
                      {record.model_version}
                    </span>
                    <span className="algo-job-duration">
                      {formatDuration(
                        time(record.finished_at) - time(record.started_at),
                      )}
                    </span>
                    {record.error_message && (
                      <p className="algo-job-error">{record.error_message}</p>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          </li>
        );
      })}
    </ol>
  );
}
