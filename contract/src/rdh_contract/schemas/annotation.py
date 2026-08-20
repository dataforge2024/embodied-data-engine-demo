"""核验与标注。

三个人工环节（交互④，Tool ↔ Platform）：

1. **核验** — 判断数据本身可用性 → :class:`VerifyResult`
2. **标注** — 编辑动作分段与描述 → :class:`AnnotationSubmit`
3. **审核** — 判断标注质量 → :class:`ReviewResult`
"""

from datetime import datetime

from pydantic import Field, model_validator

from ..enums import ReviewDecision
from .base import ContractModel
from .episode import Segment


class VerifyResult(ContractModel):
    """核验结果。

    ``APPROVE`` → Episode 进 ``annotation_processing``（送标处理，异步）；
    ``REJECT`` → 进 ``rejected``（终态）。

    通过后不直连 ``annotation_pending`` —— 中间有一个送标环节，见
    ``openspec/changes/manual-workflow-progression/design.md`` 第 1 节。
    """

    episode_id: str = Field(description="被核验的 Episode ID")
    decision: ReviewDecision = Field(description="裁决：通过 / 打回")
    reason: str | None = Field(default=None, max_length=2000, description="打回原因")
    checked_topics: tuple[str, ...] = Field(default=(), description="已核验的 topic")
    verified_by: str = Field(description="核验人 user_id")
    verified_at: datetime = Field(description="核验时间（UTC）")

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> "VerifyResult":
        """打回必须给出原因。"""
        if self.decision is ReviewDecision.REJECT and not self.reason:
            raise ValueError("核验打回必须填写 reason")
        return self


class AnnotationSubmit(ContractModel):
    """提交标注（Tool → Platform）。

    ``segments`` 是标注人编辑后的**全量**分段，不是增量补丁：
    Platform 整体替换 Episode 的 segments，避免并发编辑下的合并歧义。
    """

    episode_id: str = Field(description="被标注的 Episode ID")
    segments: tuple[Segment, ...] = Field(min_length=1, description="编辑后的全量分段")
    notes: str | None = Field(default=None, max_length=2000, description="标注备注")

    @model_validator(mode="after")
    def _check_no_overlap(self) -> "AnnotationSubmit":
        """分段之间不得重叠（按时间排序后逐对比较）。"""
        ordered = sorted(self.segments, key=lambda s: s.start_ms)
        for prev, curr in zip(ordered, ordered[1:], strict=False):
            if curr.start_ms < prev.end_ms:
                raise ValueError(
                    f"分段重叠：{prev.segment_id}({prev.start_ms}-{prev.end_ms}) 与 "
                    f"{curr.segment_id}({curr.start_ms}-{curr.end_ms})"
                )
        return self


class ReviewResult(ContractModel):
    """标注审核结果。

    ``APPROVE`` → Episode 进 ``published``；``REJECT`` → 退回 ``annotation_pending`` 重做
    （注意：不是进 ``rejected``，退回重做与核验打回是两回事）。
    """

    episode_id: str = Field(description="被审核的 Episode ID")
    decision: ReviewDecision = Field(description="裁决：通过 / 退回重做")
    reason: str | None = Field(default=None, max_length=2000, description="退回原因")
    reviewed_by: str = Field(description="审核人 user_id")
    reviewed_at: datetime = Field(description="审核时间（UTC）")

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> "ReviewResult":
        """退回必须给出原因。"""
        if self.decision is ReviewDecision.REJECT and not self.reason:
            raise ValueError("标注退回必须填写 reason")
        return self


class Annotation(ContractModel):
    """标注记录完整视图（含核验与审核轨迹）。"""

    annotation_id: str = Field(description="标注记录 ID（UUID）")
    episode_id: str = Field(description="所属 Episode ID")
    segments: tuple[Segment, ...] = Field(default=(), description="当前分段")
    notes: str | None = Field(default=None, description="标注备注")

    verify_result: VerifyResult | None = Field(default=None, description="核验结果")
    review_result: ReviewResult | None = Field(default=None, description="最近一次审核结果")
    revision: int = Field(default=1, ge=1, description="修订版本，每次退回重做后 +1")

    annotated_by: str | None = Field(default=None, description="标注人 user_id")
    created_at: datetime = Field(description="创建时间（UTC）")
    updated_at: datetime = Field(description="最后更新时间（UTC）")


__all__ = ["Annotation", "AnnotationSubmit", "ReviewResult", "VerifyResult"]
