/**
 * 指向 Tool（人工环节工作台）的外链。
 *
 * Platform 与 Tool 是两个独立前端应用，靠 URL 串联而不是共享代码 —— 它们可能由不同的人
 * 部署在不同域名下，所以基址走环境变量 `VITE_TOOL_BASE_URL`，不写死端口。
 *
 * **当前 Tool 还没读这些查询参数**（它的工作台切换是组件内 state，标注页要手输 Episode
 * ID）。这里先按约定把 `episode` 与 `stage` 带上：Tool 补上读取后，Platform 侧一行不用改
 * 就能深链过去。在那之前点过去只会落在 Tool 的默认工作台（核验）。
 */

import type { EpisodeStatus } from "@contract";

/** Tool 的三个人工环节工作台。 */
export type ToolStage = "verify" | "annotate" | "review";

export const TOOL_STAGE_LABELS: Record<ToolStage, string> = {
  verify: "去核验",
  annotate: "去标注",
  review: "去审核",
};

/**
 * 哪些状态该跳哪个工作台。
 *
 * 只有这三个状态在等人操作；其余状态要么在自动流水线里，要么已到终态，
 * 跳过去没有可做的事，所以不给入口。
 */
const STATUS_TO_TOOL_STAGE: Partial<Record<EpisodeStatus, ToolStage>> = {
  verification_pending: "verify",
  annotation_pending: "annotate",
  annotation_review: "review",
};

/** 该状态对应的人工环节；不需要人工介入时返回 null。 */
export function toolStageOf(status: EpisodeStatus): ToolStage | null {
  return STATUS_TO_TOOL_STAGE[status] ?? null;
}

/** Tool 的基址。未配置时退回本地开发端口。 */
export function toolBaseUrl(): string {
  const configured = import.meta.env.VITE_TOOL_BASE_URL;
  return (configured ?? "http://localhost:5174").replace(/\/$/, "");
}

/** 拼出打开某个 Episode 某个环节的 URL。 */
export function toolLink(episodeId: string, stage: ToolStage): string {
  const params = new URLSearchParams({ episode: episodeId, stage });
  return `${toolBaseUrl()}/?${params.toString()}`;
}
