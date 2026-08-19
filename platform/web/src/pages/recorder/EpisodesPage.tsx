/** 采集记录：跨任务的历史视图。
 *
 * 与任务管理页的分工 —— 那里是「任务 → 它的采集记录」的父子主视角；这里是
 * 「我采过的所有数据」，不预设从哪个任务进来，可按任务与状态筛。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import type { CollectTask, Episode, EpisodeStatus, User } from "@contract";
import { fetchEpisodes, fetchTasks, fetchUsers } from "../../api/client";
import { EpisodeTable, STATUS_LABELS } from "../../components/EpisodeTable";
import { useConsoleStream } from "../../hooks/useConsoleStream";
import "../shared.css";

/** 下拉里只列实际会出现的状态，省得堆一串永远为空的选项。 */
const FILTERABLE_STATUSES: EpisodeStatus[] = [
  "recording",
  "uploading",
  "uploaded",
  "processing",
  "verification_pending",
  "annotation_pending",
  "annotation_review",
  "published",
  "rejected",
  "failed",
];

export function EpisodesPage() {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [tasks, setTasks] = useState<CollectTask[]>([]);
  const [users, setUsers] = useState<Record<string, User>>({});
  const [taskFilter, setTaskFilter] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<EpisodeStatus | undefined>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { uploadProgress, connected } = useConsoleStream();

  const load = useCallback(async () => {
    try {
      const { items } = await fetchEpisodes({
        taskId: taskFilter,
        status: statusFilter,
      });
      setEpisodes(items);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [taskFilter, statusFilter]);

  useEffect(() => {
    void load();
    // WS 只推进度与上下线；状态流转（uploaded → processing → …）仍靠轮询兜底
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    // 任务名与用户名都是为了把 ID 翻成人看得懂的东西，查不到就退化显示 ID
    fetchTasks()
      .then(({ items }) => setTasks(items))
      .catch(() => setTasks([]));
    fetchUsers()
      .then((list) =>
        setUsers(Object.fromEntries(list.map((u) => [u.user_id, u]))),
      )
      .catch(() => setUsers({}));
  }, []);

  const taskNames = useMemo(
    () => Object.fromEntries(tasks.map((t) => [t.task_id, t.name])),
    [tasks],
  );

  const counts = useMemo(() => {
    const byStatus = episodes.reduce<Record<string, number>>((acc, ep) => {
      acc[ep.status] = (acc[ep.status] || 0) + 1;
      return acc;
    }, {});
    return {
      total: episodes.length,
      uploading: (byStatus.recording || 0) + (byStatus.uploading || 0),
      uploaded: byStatus.uploaded || 0,
      published: byStatus.published || 0,
      failed: (byStatus.failed || 0) + (byStatus.rejected || 0),
    };
  }, [episodes]);

  if (loading) return <div className="loading-state">加载中...</div>;

  return (
    <main className="workspace-main">
      <header className="workspace-header">
        <h1>采集记录</h1>
        <span className={`live-dot ${connected ? "on" : "off"}`}>
          {connected ? "实时推送已连接" : "实时推送断开（轮询兜底）"}
        </span>
      </header>

      {error && (
        <div className="error-state" role="alert">
          {error}
        </div>
      )}

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">共</div>
          <div className="stat-value">{counts.total}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">采集/上传中</div>
          <div className="stat-value">{counts.uploading}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已上传</div>
          <div className="stat-value">{counts.uploaded}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已发布</div>
          <div className="stat-value">{counts.published}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">失败/打回</div>
          <div className="stat-value">{counts.failed}</div>
        </div>
      </div>

      <div className="filter-bar">
        <Select
          allowClear
          placeholder="按任务筛选"
          style={{ minWidth: 240 }}
          value={taskFilter}
          onChange={setTaskFilter}
          options={tasks.map((t) => ({ value: t.task_id, label: t.name }))}
          notFoundContent="暂无任务"
        />
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ minWidth: 160 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={FILTERABLE_STATUSES.map((s) => ({
            value: s,
            label: STATUS_LABELS[s],
          }))}
        />
      </div>

      <EpisodeTable
        episodes={episodes}
        users={users}
        uploadProgress={uploadProgress}
        taskNames={taskNames}
        emptyText={
          taskFilter || statusFilter ? "没有符合筛选的记录" : "暂无记录"
        }
      />
    </main>
  );
}
