/** SysOps 工作区：Episode 状态分布 + Agent 在线情况。 */

import { useEffect, useState } from 'react';
import type { AgentNode } from '@contract';
import { EpisodeStatusValues } from '@contract';
import { fetchAgents, fetchEpisodeStats } from '../../api/client';

export function SysOpsPage() {
  const [stats, setStats] = useState<Record<string, number>>({});
  const [agents, setAgents] = useState<AgentNode[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEpisodeStats().then(setStats).catch((e: Error) => setError(e.message));
    fetchAgents().then(setAgents).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p role="alert">{error}</p>;

  return (
    <main>
      <h1>运维监控</h1>

      <section>
        <h2>Episode 状态分布</h2>
        <table>
          <thead>
            <tr><th>状态</th><th>数量</th></tr>
          </thead>
          <tbody>
            {EpisodeStatusValues.map((status) => (
              <tr key={status}>
                <td>{status}</td>
                <td>{stats[status] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>Agent 节点</h2>
        <ul>
          {agents.map((agent) => (
            <li key={agent.agent_id}>
              {agent.agent_id} · {agent.hostname} · {agent.online ? '在线' : '离线'}
              {agent.last_heartbeat && ` · 待上传 ${agent.last_heartbeat.pending_upload_count ?? 0}`}
            </li>
          ))}
          {agents.length === 0 && <li>暂无 Agent 注册</li>}
        </ul>
      </section>
    </main>
  );
}
