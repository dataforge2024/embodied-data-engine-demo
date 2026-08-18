/** Platform 前端外壳。四个工作区对应文档里的角色划分。 */

import { useState } from 'react';
import { TasksPage } from './pages/admin/TasksPage';
import { EpisodesPage } from './pages/recorder/EpisodesPage';
import { SysOpsPage } from './pages/sysops/SysOpsPage';
import { contractVersion } from './api/client';

type Workspace = 'admin' | 'recorder' | 'sysops';

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>('admin');

  return (
    <div className="app">
      <nav>
        <button type="button" onClick={() => setWorkspace('admin')}>任务管理</button>
        <button type="button" onClick={() => setWorkspace('recorder')}>采集记录</button>
        <button type="button" onClick={() => setWorkspace('sysops')}>运维监控</button>
        <span>契约 {contractVersion()}</span>
      </nav>
      {workspace === 'admin' && <TasksPage />}
      {workspace === 'recorder' && <EpisodesPage />}
      {workspace === 'sysops' && <SysOpsPage />}
    </div>
  );
}
