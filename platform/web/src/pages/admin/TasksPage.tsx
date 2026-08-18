/** Admin 工作区：采集任务列表与进度。 */

import { useEffect, useState } from 'react';
import type { CollectTask } from '@contract';
import { fetchTasks } from '../../api/client';

export function TasksPage() {
  const [tasks, setTasks] = useState<CollectTask[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTasks().then(({ items }) => setTasks(items)).catch((e: Error) => setError(e.message));
  }, []);

  if (error) return <p role="alert">{error}</p>;

  return (
    <main>
      <h1>采集任务</h1>
      <table>
        <thead>
          <tr><th>任务</th><th>状态</th><th>场景</th><th>进度</th></tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={task.task_id}>
              <td>{task.name}</td>
              <td>{task.status}</td>
              <td>{task.requirement.scene}</td>
              <td>
                {task.published_count ?? 0} / {task.requirement.target_episode_count}
              </td>
            </tr>
          ))}
          {tasks.length === 0 && <tr><td colSpan={4}>暂无任务</td></tr>}
        </tbody>
      </table>
    </main>
  );
}
