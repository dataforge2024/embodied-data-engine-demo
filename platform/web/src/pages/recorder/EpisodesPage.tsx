/** Recorder 工作区：Episode 列表，按状态展示流转位置。 */

import { useEffect, useState } from 'react';
import type { Episode, EpisodeStatus } from '@contract';
import { isTerminal } from '@contract';
import { fetchEpisodes } from '../../api/client';

export function EpisodesPage() {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEpisodes().then(({ items }) => setEpisodes(items)).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p role="alert">{error}</p>;

  return (
    <main>
      <h1>采集记录</h1>
      <table>
        <thead>
          <tr><th>Episode</th><th>状态</th><th>时长</th><th>分段</th><th>终态</th></tr>
        </thead>
        <tbody>
          {episodes.map((episode) => (
            <tr key={episode.episode_id}>
              <td>{episode.episode_id.slice(0, 8)}</td>
              <td>{episode.status}</td>
              <td>{episode.duration_ms ? `${(episode.duration_ms / 1000).toFixed(1)}s` : '—'}</td>
              <td>{episode.segments?.length ?? 0}</td>
              {/* 用契约导出的 isTerminal，不在前端硬编码终态列表 */}
              <td>{isTerminal(episode.status as EpisodeStatus) ? '是' : '否'}</td>
            </tr>
          ))}
          {episodes.length === 0 && <tr><td colSpan={5}>暂无记录</td></tr>}
        </tbody>
      </table>
    </main>
  );
}
