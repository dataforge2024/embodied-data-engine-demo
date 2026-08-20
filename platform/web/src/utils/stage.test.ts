/**
 * 阶段映射的不变量。
 *
 * 这些性质是阶段划分的依据（design.md 第 4a 节），改分组时容易顺手破坏：
 *
 * - 审核退回不让进度条后退 —— 标注与审核合并一格就是为了藏住这个循环
 * - 送标与待标注必须分属两格 —— 一个是等系统，一个是等人，处置完全不同
 * - 脱轨态借轨迹标出中断位置，而不是把所有格子一律标灰
 */

import { describe, expect, it } from "vitest";
import type { EpisodeStatus, TransitionRecord } from "@contract";
import {
  STAGE_LABELS,
  STAGE_ORDER,
  countByStage,
  derailedAt,
  isDerailed,
  stageOf,
  stageStates,
} from "./stage";

/** 造一条流转记录。只有状态字段参与断言，其余给占位值。 */
function record(
  from: EpisodeStatus,
  to: EpisodeStatus,
  occurredAt = "2026-08-20T10:00:00Z",
): TransitionRecord {
  return {
    episode_id: "ep-1",
    from_status: from,
    to_status: to,
    actor: { actor_type: "system", user_id: null, system_component: "test" },
    reason: null,
    occurred_at: occurredAt,
  };
}

describe("六阶段划分", () => {
  it("有六格且顺序固定", () => {
    expect(STAGE_ORDER).toHaveLength(6);
    expect(STAGE_ORDER).toEqual([
      "collect_manual",
      "collect_auto",
      "inspect_manual",
      "annotate_auto",
      "annotate_manual",
      "done",
    ]);
  });

  it("每格都有标签", () => {
    for (const stage of STAGE_ORDER) {
      expect(STAGE_LABELS[stage]).toBeTruthy();
    }
  });

  it("送标处理独占一格，不与待标注合并", () => {
    // 合并的话进度条上「算子在跑」与「人可以开始标了」分不开
    expect(stageOf("annotation_processing")).toBe("annotate_auto");
    expect(stageOf("annotation_pending")).toBe("annotate_manual");
    expect(stageOf("annotation_processing")).not.toBe(
      stageOf("annotation_pending"),
    );
  });

  it("标注与审核同属一格", () => {
    expect(stageOf("annotation_review")).toBe(stageOf("annotation_pending"));
  });

  it("解析与质检分属两格", () => {
    expect(stageOf("processing")).toBe("collect_auto");
    expect(stageOf("verification_pending")).toBe("inspect_manual");
  });

  it("脱轨态不落在任何格上", () => {
    expect(stageOf("failed")).toBeNull();
    expect(stageOf("rejected")).toBeNull();
    expect(isDerailed("failed")).toBe(true);
    expect(isDerailed("rejected")).toBe(true);
    expect(isDerailed("published")).toBe(false);
  });
});

describe("审核退回时进度条不后退", () => {
  it("退回前后停在同一格", () => {
    const before = stageStates("annotation_review");
    const after = stageStates("annotation_pending");
    expect(after).toEqual(before);
  });

  it("退回后已走过的格子仍算走过", () => {
    const states = stageStates("annotation_pending");
    expect(states.collect_manual).toBe("done");
    expect(states.collect_auto).toBe("done");
    expect(states.inspect_manual).toBe("done");
    expect(states.annotate_auto).toBe("done");
    expect(states.annotate_manual).toBe("current");
    expect(states.done).toBe("pending");
  });
});

describe("stageStates 的正常推进", () => {
  it("当前格之前是 done、之后是 pending", () => {
    const states = stageStates("verification_pending");
    expect(states.collect_manual).toBe("done");
    expect(states.collect_auto).toBe("done");
    expect(states.inspect_manual).toBe("current");
    expect(states.annotate_auto).toBe("pending");
    expect(states.done).toBe("pending");
  });

  it("送标中时待标注那格还没到", () => {
    const states = stageStates("annotation_processing");
    expect(states.annotate_auto).toBe("current");
    expect(states.annotate_manual).toBe("pending");
  });

  it("published 时最后一格是 current", () => {
    const states = stageStates("published");
    expect(states.done).toBe("current");
    expect(states.annotate_manual).toBe("done");
  });
});

describe("脱轨定位", () => {
  it("没轨迹时全格 blocked", () => {
    const states = stageStates("failed");
    for (const stage of STAGE_ORDER) {
      expect(states[stage]).toBe("blocked");
    }
  });

  it("空轨迹等同于没轨迹", () => {
    const states = stageStates("failed", []);
    for (const stage of STAGE_ORDER) {
      expect(states[stage]).toBe("blocked");
    }
  });

  it("从轨迹读出死在送标处理", () => {
    const history = [
      record("recording", "uploading"),
      record("uploading", "uploaded"),
      record("uploaded", "processing"),
      record("processing", "verification_pending"),
      record("verification_pending", "annotation_processing"),
      record("annotation_processing", "failed"),
    ];
    expect(derailedAt(history)).toBe("annotate_auto");

    const states = stageStates("failed", history);
    // 中断格之前的仍算走过 —— 这正是原来「一律 blocked」丢掉的信息
    expect(states.collect_manual).toBe("done");
    expect(states.collect_auto).toBe("done");
    expect(states.inspect_manual).toBe("done");
    expect(states.annotate_auto).toBe("blocked");
    expect(states.annotate_manual).toBe("blocked");
    expect(states.done).toBe("blocked");
  });

  it("质检打回定位在质检格", () => {
    const history = [
      record("recording", "uploading"),
      record("uploading", "uploaded"),
      record("uploaded", "processing"),
      record("processing", "verification_pending"),
      record("verification_pending", "rejected"),
    ];
    expect(derailedAt(history)).toBe("inspect_manual");

    const states = stageStates("rejected", history);
    expect(states.collect_auto).toBe("done");
    expect(states.inspect_manual).toBe("blocked");
    expect(states.annotate_auto).toBe("blocked");
  });

  it("质检打回与送标失败落在不同格", () => {
    const rejected = stageStates("rejected", [
      record("processing", "verification_pending"),
      record("verification_pending", "rejected"),
    ]);
    const failed = stageStates("failed", [
      record("verification_pending", "annotation_processing"),
      record("annotation_processing", "failed"),
    ]);
    // 打回时质检格就是中断点；送标失败时质检格已走过
    expect(rejected.inspect_manual).toBe("blocked");
    expect(failed.inspect_manual).toBe("done");
  });

  it("死在第一格时没有 done 的格子", () => {
    const states = stageStates("failed", [record("uploading", "failed")]);
    expect(derailedAt([record("uploading", "failed")])).toBe("collect_manual");
    for (const stage of STAGE_ORDER) {
      expect(states[stage]).toBe("blocked");
    }
  });
});

describe("countByStage", () => {
  it("六格加脱轨都计数", () => {
    const counts = countByStage([
      "recording",
      "uploading",
      "processing",
      "verification_pending",
      "annotation_processing",
      "annotation_pending",
      "annotation_review",
      "published",
      "failed",
      "rejected",
    ]);
    expect(counts.collect_manual).toBe(2);
    expect(counts.collect_auto).toBe(1);
    expect(counts.inspect_manual).toBe(1);
    expect(counts.annotate_auto).toBe(1);
    expect(counts.annotate_manual).toBe(2);
    expect(counts.done).toBe(1);
    expect(counts.derailed).toBe(2);
  });

  it("空输入全为零", () => {
    const counts = countByStage([]);
    for (const stage of STAGE_ORDER) {
      expect(counts[stage]).toBe(0);
    }
    expect(counts.derailed).toBe(0);
  });

  it("总数等于输入条数", () => {
    const statuses: EpisodeStatus[] = [
      "recording",
      "annotation_processing",
      "failed",
    ];
    const counts = countByStage(statuses);
    const total =
      STAGE_ORDER.reduce((sum, stage) => sum + counts[stage], 0) +
      counts.derailed;
    expect(total).toBe(statuses.length);
  });
});
