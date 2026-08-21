/**
 * 标注页面（交互④）。
 *
 * 预标注分段作为起点：标注人在算子结果上修改，而不是从零开始画。
 *
 * `annotated_by` 不由前端传 —— Platform 从 JWT 取（review.py 路由里），
 * 客户端传 user_id 反而给了伪造空间。
 */

import { useCallback, useEffect, useState } from "react";
import type { Episode } from "@contract";
import { MultiViewPlayer } from "../components/player/MultiViewPlayer";
import { Timeline } from "../components/timeline/Timeline";
import { useSegmentEditor } from "../hooks/useSegmentEditor";
import { fetchEpisode, submitAnnotation } from "../api/client";
import "./workspace.css";

interface Props {
  readonly episodeId: string;
  readonly onSubmitted?: () => void;
}

export function AnnotatePage({ episodeId, onSubmitted }: Props) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [positionMs, setPositionMs] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEpisode(episodeId)
      .then(setEpisode)
      .catch((e: Error) => setError(e.message));
  }, [episodeId]);

  const durationMs = episode?.duration_ms ?? 0;
  const editor = useSegmentEditor(episode?.segments ?? [], durationMs);
  const selected =
    editor.segments.find((s) => s.segment_id === selectedId) ?? null;

  const handleSubmit = useCallback(async () => {
    // 空分段拦在这里而不只靠 disabled：按钮禁用不解释原因，问题清单要能读
    if (!editor.validation.valid) {
      setError(`无法提交：${editor.validation.problems.join("；")}`);
      return;
    }
    try {
      await submitAnnotation({
        episode_id: episodeId,
        segments: editor.segments,
        notes: notes.trim() || null,
      });
      setError(null);
      onSubmitted?.();
    } catch (e) {
      setError((e as Error).message);
    }
  }, [editor.segments, editor.validation, episodeId, notes, onSubmitted]);

  if (error && !episode) return <p role="alert">加载失败：{error}</p>;
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

      {selected ? (
        <fieldset className="segment-form">
          <legend>分段 {selected.segment_id.slice(0, 8)}</legend>
          <label htmlFor="action-label">动作标签</label>
          <input
            id="action-label"
            value={selected.action_label ?? ""}
            placeholder="如 grasp / place"
            onChange={(event) =>
              editor.relabel(
                selected.segment_id,
                event.target.value,
                selected.description ?? undefined,
              )
            }
          />

          <label htmlFor="action-description">动作描述</label>
          <textarea
            id="action-description"
            value={selected.description ?? ""}
            placeholder="自然语言描述这段动作"
            onChange={(event) =>
              editor.relabel(
                selected.segment_id,
                selected.action_label ?? "",
                event.target.value,
              )
            }
          />

          <p className="segment-source">
            {selected.source ? `算子产出：${selected.source}` : "人工标注"}
          </p>
        </fieldset>
      ) : (
        <p className="hint">在时间轴上选中一个分段以编辑标签与描述</p>
      )}

      <label htmlFor="episode-notes">整条备注</label>
      <textarea
        id="episode-notes"
        value={notes}
        placeholder="对整条 Episode 的说明（可留空）"
        onChange={(event) => setNotes(event.target.value)}
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
        <button type="button" className="primary" onClick={handleSubmit}>
          提交标注
        </button>
      </div>

      {error && <p role="alert">{error}</p>}
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
