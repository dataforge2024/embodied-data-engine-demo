/**
 * 标注页面（交互④）。
 *
 * 预标注分段作为起点：标注人在算子结果上修改，而不是从零开始画。
 */

import { useCallback, useEffect, useState } from 'react';
import type { Episode } from '@contract';
import { MultiViewPlayer } from '../components/player/MultiViewPlayer';
import { Timeline } from '../components/timeline/Timeline';
import { useSegmentEditor } from '../hooks/useSegmentEditor';
import { fetchEpisode, submitAnnotation } from '../api/client';

interface Props {
  readonly episodeId: string;
  readonly onSubmitted?: () => void;
}

export function AnnotatePage({ episodeId, onSubmitted }: Props) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [positionMs, setPositionMs] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEpisode(episodeId).then(setEpisode).catch((e: Error) => setError(e.message));
  }, [episodeId]);

  const durationMs = episode?.duration_ms ?? 0;
  const editor = useSegmentEditor(episode?.segments ?? [], durationMs);

  const handleSubmit = useCallback(async () => {
    if (!editor.validation.valid) return;
    try {
      await submitAnnotation({ episode_id: episodeId, segments: editor.segments });
      onSubmitted?.();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [editor.segments, editor.validation.valid, episodeId, onSubmitted]);

  if (error) return <p role="alert">加载失败：{error}</p>;
  if (!episode) return <p>加载中…</p>;

  return (
    <main className="annotate-page">
      <h1>标注 · {episode.episode_id.slice(0, 8)}</h1>
      <MultiViewPlayer
        streams={episode.streams ?? []}
        durationMs={durationMs}
        onPositionChange={setPositionMs}
      />
      <Timeline
        segments={editor.segments}
        durationMs={durationMs}
        positionMs={positionMs}
        selectedId={selectedId}
        onSeek={setPositionMs}
        onSelect={setSelectedId}
        onResize={editor.resize}
      />
      <div className="actions">
        <button type="button" disabled={!editor.canUndo} onClick={editor.undo}>
          撤销
        </button>
        <button type="button" disabled={!editor.canRedo} onClick={editor.redo}>
          重做
        </button>
        <button
          type="button"
          disabled={!selectedId}
          onClick={() => selectedId && editor.split(selectedId, positionMs)}
        >
          在播放头切分
        </button>
        <button type="button" disabled={!editor.validation.valid} onClick={handleSubmit}>
          提交标注
        </button>
      </div>
      {!editor.validation.valid && (
        <ul className="problems">
          {editor.validation.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </main>
  );
}
