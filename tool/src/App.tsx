/** Tool 应用外壳。三个工作台对应三个人工环节。 */

import { useState } from 'react';
import { AnnotatePage } from './pages/AnnotatePage';
import { ReviewPage } from './pages/ReviewPage';
import { VerifyPage } from './pages/VerifyPage';
import { contractVersion } from './api/client';

type Workspace = 'verify' | 'annotate' | 'review';

export function App() {
  const [workspace, setWorkspace] = useState<Workspace>('verify');
  const [episodeId, setEpisodeId] = useState('');
  const currentUser = 'tool-operator';

  return (
    <div className="app">
      <nav>
        <button type="button" onClick={() => setWorkspace('verify')}>核验</button>
        <button type="button" onClick={() => setWorkspace('annotate')}>标注</button>
        <button type="button" onClick={() => setWorkspace('review')}>审核</button>
        <span className="version">契约 {contractVersion()}</span>
      </nav>

      {workspace === 'verify' && <VerifyPage verifiedBy={currentUser} />}
      {workspace === 'review' && <ReviewPage reviewedBy={currentUser} />}
      {workspace === 'annotate' && (
        <>
          <input
            value={episodeId}
            onChange={(event) => setEpisodeId(event.target.value)}
            placeholder="Episode ID"
          />
          {episodeId && <AnnotatePage episodeId={episodeId} />}
        </>
      )}
    </div>
  );
}
