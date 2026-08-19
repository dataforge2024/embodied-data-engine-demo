/** 任务详情：一个任务下的所有子任务（采集记录）。
 *
 * 「子任务」在数据上就是 Episode —— 一次采集上传即建一条。这里是它们的归属视图，
 * 每条都能看到自己走到哪个阶段、卡在哪个子状态。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Select, message } from "antd";
import type {
  AgentNode,
  CollectTask,
  Episode,
  EpisodeStatus,
  User,
} from "@contract";
import {
  assignTask,
  fetchAgents,
  fetchEpisodes,
  fetchTask,
  fetchUsers,
} from "../../api/client";
import { EpisodeTable, STATUS_LABELS } from "../../components/EpisodeTable";
import { StageBar } from "../../components/StageBar";
import { useConsoleStream } from "../../hooks/useConsoleStream";
import { formatFull } from "../../utils/datetime";
import {
  STAGE_HINTS,
  STAGE_LABELS,
  STAGE_ORDER,
  countByStage,
} from "../../utils/stage";
import "../shared.css";

interface TaskDetailPageProps {
  taskId: string;
  onBack: () => void;
}

export function TaskDetailPage({ taskId, onBack }: TaskDetailPageProps) {
  const [task, setTask] = useState<CollectTask | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [users, setUsers] = useState<Record<string, User>>({});
  const [statusFilter, setStatusFilter] = useState<EpisodeStatus | undefined>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { agentOnline, uploadProgress } = useConsoleStream();

  const load = useCallback(async () => {
    try {
      const [detail, { items }] = await Promise.all([
        fetchTask(taskId),
        fetchEpisodes({ taskId, status: statusFilter }),
      ]);
      setTask(detail);
      setEpisodes(items);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [taskId, statusFilter]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    fetchAgents()
      .then(setAgents)
      .catch(() => setAgents([]));
    fetchUsers()
      .then((list) =>
        setUsers(Object.fromEntries(list.map((u) => [u.user_id, u]))),
      )
      .catch(() => setUsers({}));
  }, []);

  // 阶段汇总用未筛选的全量算，否则筛了状态后汇总会自相矛盾
  const [allStatuses, setAllStatuses] = useState<EpisodeStatus[]>([]);
  useEffect(() => {
    fetchEpisodes({ taskId })
      .then(({ items }) =>
        setAllStatuses(items.map((e) => e.status as EpisodeStatus)),
      )
      .catch(() => setAllStatuses([]));
  }, [taskId, episodes.length]);

  const stageCounts = useMemo(() => countByStage(allStatuses), [allStatuses]);

  const onlineOptions = agents
    .filter((a) => agentOnline[a.agent_id] ?? a.online)
    .map((a) => ({
      value: a.agent_id,
      label: `${a.agent_id} · ${a.hostname}`,
    }));

  const handleAssign = async (agentId: string) => {
    try {
      await assignTask(taskId, agentId);
      message.success(`已下发到 ${agentId}`);
      void load();
    } catch (e) {
      message.error(e instanceof Error ? e.message : "分派失败");
    }
  };

  if (loading) return <div className="loading-state">加载中...</div>;
  if (error)
    return (
      <div className="error-state" role="alert">
        {error}
      </div>
    );
  if (task === null) return <div className="error-state">任务不存在</div>;

  const req = task.requirement;
  const currentAgent =
    task.assignments?.[task.assignments.length - 1]?.agent_id ?? null;

  return (
    <main className="workspace-main">
      <div className="breadcrumb">
        <button type="button" className="link-btn" onClick={onBack}>
          ← 采集任务
        </button>
        <span className="breadcrumb-sep">/</span>
        <span>{task.name}</span>
      </div>

      <header className="workspace-header">
        <h1>{task.name}</h1>
        <Select
          placeholder={currentAgent ? "改派给…" : "分派给…"}
          style={{ minWidth: 220 }}
          value={undefined}
          options={onlineOptions}
          onChange={handleAssign}
          notFoundContent="无在线 Agent"
        />
      </header>

      {task.description && <p className="task-desc">{task.description}</p>}

      <dl className="meta-grid">
        <div>
          <dt>机型</dt>
          <dd className="mono-cell">{req.robot_model}</dd>
        </div>
        <div>
          <dt>场景</dt>
          <dd>{req.scene}</dd>
        </div>
        <div>
          <dt>时长要求</dt>
          <dd className="mono-cell">
            {req.min_duration_ms / 1000}s – {req.max_duration_ms / 1000}s
          </dd>
        </div>
        <div>
          <dt>必需 Topics</dt>
          <dd className="mono-cell topics-cell">
            {req.required_topics.join(", ")}
          </dd>
        </div>
        <div>
          <dt>负责 Agent</dt>
          <dd className="mono-cell">
            {currentAgent ?? <span className="empty-value">未分派</span>}
          </dd>
        </div>
        <div>
          <dt>创建时间</dt>
          <dd className="mono-cell">{formatFull(task.created_at)}</dd>
        </div>
      </dl>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">子任务</div>
          <div className="stat-value">{allStatuses.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">目标条数</div>
          <div className="stat-value">{req.target_episode_count}</div>
        </div>
        {STAGE_ORDER.map((stage) => (
          <div className="stat-card" key={stage} title={STAGE_HINTS[stage]}>
            <div className="stat-label">{STAGE_LABELS[stage]}</div>
            <div className="stat-value">{stageCounts[stage]}</div>
          </div>
        ))}
        {stageCounts.derailed > 0 && (
          <div className="stat-card derailed-card">
            <div className="stat-label">失败/打回</div>
            <div className="stat-value">{stageCounts.derailed}</div>
          </div>
        )}
      </div>

      <div className="filter-bar">
        <Select
          allowClear
          placeholder="按子状态筛选"
          style={{ minWidth: 180 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={Array.from(new Set(allStatuses)).map((s) => ({
            value: s,
            label: STATUS_LABELS[s],
          }))}
          notFoundContent="暂无记录"
        />
      </div>

      <EpisodeTable
        episodes={episodes}
        users={users}
        uploadProgress={uploadProgress}
        showTaskColumn={false}
        emptyText={
          statusFilter
            ? "没有符合筛选的子任务"
            : "该任务还没有子任务 —— Agent 上传一个文件即建一条"
        }
      />

      <section className="legend">
        <div className="legend-title">阶段说明</div>
        <StageBar status={"recording" as EpisodeStatus} />
        <ul className="legend-list">
          {STAGE_ORDER.map((stage) => (
            <li key={stage}>
              <strong>{STAGE_LABELS[stage]}</strong>
              <span>{STAGE_HINTS[stage]}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
