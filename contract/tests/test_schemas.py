"""共享数据模型的校验器与派生属性测试。

契约模型里的 validator 是**跨模块的守门人**：Tool 提交重叠分段、Agent 报负数时长、
算子报失败却不给原因，都应该在边界处被拒，而不是流进数据库。

另覆盖 WS 协议的判别式解析 —— Agent 与 Platform 靠它区分消息方向与类型。
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rdh_contract.enums import (
    AlgoOperator,
    EpisodeStatus,
    JobStatus,
    ReviewDecision,
    Role,
    UploadStatus,
)
from rdh_contract.schemas import (
    AgentHeartbeat,
    AnnotationSubmit,
    ApiResponse,
    Episode,
    ErrorDetail,
    PageMeta,
    ReviewResult,
    Segment,
    TaskRequirement,
    TokenPayload,
    UploadProgress,
    User,
    VerifyResult,
)
from rdh_contract.schemas.scheduler import AlgoJobResult, AlgoResultCallback
from rdh_contract.ws import (
    CONSOLE_ADAPTER,
    DOWNSTREAM_ADAPTER,
    UPSTREAM_ADAPTER,
    ConsoleAgentStatusFrame,
    ConsoleUploadProgressFrame,
    HeartbeatFrame,
    MessageType,
    RegisterFrame,
    TaskPushFrame,
)
from rdh_contract.ws.protocol import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_TIMEOUT_SECONDS,
    WS_PROTOCOL_VERSION,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def make_segment(segment_id: str = "s1", start: int = 0, end: int = 1000) -> Segment:
    """构造一个合法分段。"""
    return Segment(segment_id=segment_id, start_ms=start, end_ms=end)


@pytest.mark.unit
class TestSegment:
    """分段是 Algo / Tool / Platform 三方共享的结构，约束最需要收紧。"""

    def test_duration_computed(self) -> None:
        """时长由区间派生。"""
        assert make_segment(start=200, end=1700).duration_ms == 1500

    def test_rejects_zero_length(self) -> None:
        """零长分段无意义。"""
        with pytest.raises(ValidationError):
            Segment(segment_id="s", start_ms=500, end_ms=500)

    def test_rejects_reversed_range(self) -> None:
        """结束早于开始必须拒绝。"""
        with pytest.raises(ValidationError, match="必须大于"):
            Segment(segment_id="s", start_ms=900, end_ms=100)

    def test_rejects_negative_start(self) -> None:
        """起点不得为负。"""
        with pytest.raises(ValidationError):
            Segment(segment_id="s", start_ms=-1, end_ms=100)

    def test_confidence_bounded(self) -> None:
        """置信度必须在 [0, 1]。"""
        with pytest.raises(ValidationError):
            Segment(segment_id="s", start_ms=0, end_ms=10, confidence=1.5)

    def test_algo_source_recorded(self) -> None:
        """算子来源可追溯。"""
        seg = Segment(
            segment_id="s", start_ms=0, end_ms=10, source=AlgoOperator.PREANNOTATE, confidence=0.8
        )
        assert seg.source is AlgoOperator.PREANNOTATE

    def test_manual_segment_has_no_source(self) -> None:
        """人工分段 source 为 None。"""
        assert make_segment().source is None

    def test_is_immutable(self) -> None:
        """模型冻结，改动必须走 model_copy。"""
        seg = make_segment()
        with pytest.raises(ValidationError):
            seg.start_ms = 50  # type: ignore[misc]

    def test_model_copy_produces_new_object(self) -> None:
        """更新返回新对象，原对象不变。"""
        seg = make_segment()
        updated = seg.model_copy(update={"action_label": "grasp"})
        assert updated.action_label == "grasp"
        assert seg.action_label is None

    def test_rejects_unknown_field(self) -> None:
        """未声明字段被拒，契约漂移立即暴露。"""
        with pytest.raises(ValidationError):
            Segment(segment_id="s", start_ms=0, end_ms=10, bogus="x")  # type: ignore[call-arg]


@pytest.mark.unit
class TestAnnotationSubmit:
    """标注提交的重叠校验 —— 重叠分段会让训练集出现矛盾样本。"""

    def test_accepts_adjacent_segments(self) -> None:
        """首尾相接不算重叠。"""
        submit = AnnotationSubmit(
            episode_id="e1",
            segments=(make_segment("a", 0, 100), make_segment("b", 100, 200)),
        )
        assert len(submit.segments) == 2

    def test_rejects_overlapping_segments(self) -> None:
        """重叠必须拒绝。"""
        with pytest.raises(ValidationError, match="分段重叠"):
            AnnotationSubmit(
                episode_id="e1",
                segments=(make_segment("a", 0, 150), make_segment("b", 100, 200)),
            )

    def test_detects_overlap_regardless_of_input_order(self) -> None:
        """乱序输入也能检出重叠（校验前先排序）。"""
        with pytest.raises(ValidationError, match="分段重叠"):
            AnnotationSubmit(
                episode_id="e1",
                segments=(make_segment("b", 100, 200), make_segment("a", 0, 150)),
            )

    def test_accepts_empty_segments(self) -> None:
        """允许无分段提交（比如整条 Episode 无有效动作）。"""
        assert AnnotationSubmit(episode_id="e1", segments=()).segments == ()

    def test_accepts_single_segment(self) -> None:
        """单个分段合法。"""
        assert len(AnnotationSubmit(episode_id="e1", segments=(make_segment(),)).segments) == 1


@pytest.mark.unit
class TestReviewReasons:
    """打回/退回必须给原因，否则采集人无从改进。"""

    def test_verify_reject_requires_reason(self) -> None:
        """核验打回缺原因被拒。"""
        with pytest.raises(ValidationError, match="必须填写 reason"):
            VerifyResult(
                episode_id="e1",
                decision=ReviewDecision.REJECT,
                verified_by="u1",
                verified_at=NOW,
            )

    def test_verify_approve_needs_no_reason(self) -> None:
        """核验通过无需原因。"""
        result = VerifyResult(
            episode_id="e1", decision=ReviewDecision.APPROVE, verified_by="u1", verified_at=NOW
        )
        assert result.reason is None

    def test_review_reject_requires_reason(self) -> None:
        """标注退回缺原因被拒。"""
        with pytest.raises(ValidationError, match="必须填写 reason"):
            ReviewResult(
                episode_id="e1",
                decision=ReviewDecision.REJECT,
                reviewed_by="u1",
                reviewed_at=NOW,
            )

    def test_review_approve_needs_no_reason(self) -> None:
        """审核通过无需原因。"""
        result = ReviewResult(
            episode_id="e1", decision=ReviewDecision.APPROVE, reviewed_by="u1", reviewed_at=NOW
        )
        assert result.decision is ReviewDecision.APPROVE


@pytest.mark.unit
class TestTaskRequirement:
    """采集要求的区间校验。"""

    def test_valid_requirement(self) -> None:
        """合法要求可构造。"""
        req = TaskRequirement(
            robot_model="rm-1",
            scene="kitchen",
            required_topics=("/camera/front",),
            min_duration_ms=1000,
            max_duration_ms=60000,
            target_episode_count=100,
        )
        assert req.target_episode_count == 100

    def test_rejects_inverted_duration_range(self) -> None:
        """上限不大于下限必须拒绝。"""
        with pytest.raises(ValidationError, match="必须大于"):
            TaskRequirement(
                robot_model="rm-1",
                scene="kitchen",
                required_topics=(),
                min_duration_ms=60000,
                max_duration_ms=1000,
                target_episode_count=10,
            )

    def test_rejects_zero_target_count(self) -> None:
        """目标条数必须为正。"""
        with pytest.raises(ValidationError):
            TaskRequirement(
                robot_model="rm-1",
                scene="kitchen",
                required_topics=(),
                min_duration_ms=0,
                max_duration_ms=1000,
                target_episode_count=0,
            )


@pytest.mark.unit
class TestUploadProgress:
    """断点续传依赖 missing_parts 计算正确。"""

    def test_progress_ratio(self) -> None:
        """进度按已完成分片计算。"""
        progress = UploadProgress(
            episode_id="e1",
            object_key="k",
            total_parts=4,
            uploaded_parts=(1, 2),
            status=UploadStatus.IN_PROGRESS,
        )
        assert progress.progress_ratio == 0.5

    def test_missing_parts_identifies_gaps(self) -> None:
        """乱序完成时也能算出缺口（断电恢复的核心）。"""
        progress = UploadProgress(
            episode_id="e1",
            object_key="k",
            total_parts=5,
            uploaded_parts=(1, 3, 5),
            status=UploadStatus.IN_PROGRESS,
        )
        assert progress.missing_parts == (2, 4)

    def test_no_missing_parts_when_complete(self) -> None:
        """全部完成时无缺口。"""
        progress = UploadProgress(
            episode_id="e1",
            object_key="k",
            total_parts=2,
            uploaded_parts=(1, 2),
            status=UploadStatus.COMPLETED,
        )
        assert progress.missing_parts == ()
        assert progress.progress_ratio == 1.0

    def test_all_parts_missing_at_start(self) -> None:
        """未开始时全部待传。"""
        progress = UploadProgress(
            episode_id="e1", object_key="k", total_parts=3, status=UploadStatus.PENDING
        )
        assert progress.missing_parts == (1, 2, 3)
        assert progress.progress_ratio == 0.0


@pytest.mark.unit
class TestAlgoResults:
    """算子结果的失败原因约束与聚合判断。"""

    def _result(self, status: JobStatus, error: str | None = None) -> AlgoJobResult:
        return AlgoJobResult(
            job_id="j1",
            episode_id="e1",
            operator=AlgoOperator.PREANNOTATE,
            status=status,
            model_version="v1.2.0",
            error_message=error,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=30),
        )

    def test_failure_requires_error_message(self) -> None:
        """失败必须给原因，否则无法排障。"""
        with pytest.raises(ValidationError, match="error_message"):
            self._result(JobStatus.FAILED)

    def test_timeout_requires_error_message(self) -> None:
        """超时同样必须给原因。"""
        with pytest.raises(ValidationError, match="error_message"):
            self._result(JobStatus.TIMEOUT)

    def test_success_needs_no_error_message(self) -> None:
        """成功无需原因。"""
        assert self._result(JobStatus.SUCCEEDED).error_message is None

    def test_duration_computed(self) -> None:
        """耗时由起止时间派生。"""
        assert self._result(JobStatus.SUCCEEDED).duration_seconds == 30.0

    def test_callback_all_succeeded_true(self) -> None:
        """全部成功时聚合判断为 True。"""
        callback = AlgoResultCallback(
            episode_id="e1",
            results=(self._result(JobStatus.SUCCEEDED),),
            pipeline_complete=True,
            reported_at=NOW,
        )
        assert callback.all_succeeded is True

    def test_callback_all_succeeded_false_on_any_failure(self) -> None:
        """任一失败即为 False。"""
        callback = AlgoResultCallback(
            episode_id="e1",
            results=(
                self._result(JobStatus.SUCCEEDED),
                self._result(JobStatus.FAILED, "CUDA OOM"),
            ),
            pipeline_complete=True,
            reported_at=NOW,
        )
        assert callback.all_succeeded is False

    def test_callback_rejects_empty_results(self) -> None:
        """空结果回调无意义。"""
        with pytest.raises(ValidationError):
            AlgoResultCallback(episode_id="e1", results=(), pipeline_complete=True, reported_at=NOW)


@pytest.mark.unit
class TestPaginationAndEnvelope:
    """响应封套与分页派生属性。"""

    def test_total_pages_rounds_up(self) -> None:
        """总页数向上取整。"""
        assert PageMeta(total=21, page=1, limit=20).total_pages == 2

    def test_total_pages_exact_division(self) -> None:
        """整除时不多算一页。"""
        assert PageMeta(total=40, page=1, limit=20).total_pages == 2

    def test_total_pages_zero_when_empty(self) -> None:
        """无记录时零页。"""
        assert PageMeta(total=0, page=1, limit=20).total_pages == 0

    def test_has_next_true_on_first_of_two(self) -> None:
        """还有下一页。"""
        assert PageMeta(total=40, page=1, limit=20).has_next is True

    def test_has_next_false_on_last(self) -> None:
        """末页无下一页。"""
        assert PageMeta(total=40, page=2, limit=20).has_next is False

    def test_limit_upper_bound_enforced(self) -> None:
        """每页上限受控，防止一次拉全库。"""
        with pytest.raises(ValidationError):
            PageMeta(total=1, page=1, limit=500)

    def test_page_starts_at_one(self) -> None:
        """页码从 1 开始。"""
        with pytest.raises(ValidationError):
            PageMeta(total=1, page=0, limit=20)

    def test_success_envelope_carries_data(self) -> None:
        """成功响应带数据、无错误。"""
        resp: ApiResponse[Segment] = ApiResponse(success=True, data=make_segment())
        assert resp.data is not None
        assert resp.error is None

    def test_error_envelope_carries_error(self) -> None:
        """失败响应带错误、无数据。"""
        resp: ApiResponse[Segment] = ApiResponse(
            success=False,
            error=ErrorDetail(code="EPISODE_NOT_FOUND", message="Episode 不存在"),
        )
        assert resp.data is None
        assert resp.error is not None
        assert resp.error.code == "EPISODE_NOT_FOUND"


@pytest.mark.unit
class TestUserAndAuth:
    """角色判断与凭据模型。"""

    def test_has_role_true(self) -> None:
        """具备角色时返回 True。"""
        user = User(
            user_id="u1",
            username="alice",
            display_name="Alice",
            roles=(Role.ADMIN, Role.LAB),
            created_at=NOW,
        )
        assert user.has_role(Role.ADMIN) is True

    def test_has_role_false(self) -> None:
        """不具备角色时返回 False。"""
        user = User(
            user_id="u1",
            username="alice",
            display_name="Alice",
            roles=(Role.RECORDER,),
            created_at=NOW,
        )
        assert user.has_role(Role.ADMIN) is False

    def test_user_model_has_no_credential_fields(self) -> None:
        """用户视图不含任何凭据字段 —— 该模型会返回给前端。"""
        forbidden = {"password", "password_hash", "hashed_password", "secret", "token"}
        assert not forbidden & set(User.model_fields)

    def test_token_payload_uses_jwt_claim_names(self) -> None:
        """沿用 JWT 注册声明名，便于标准库解析。"""
        payload = TokenPayload(sub="u1", roles=(Role.ADMIN,), exp=1, iat=0)
        assert payload.sub == "u1"
        assert {"sub", "exp", "iat"} <= set(TokenPayload.model_fields)


@pytest.mark.unit
class TestEpisodeDefaults:
    """Episode 的默认值：新建时各集合应为空而非 None。"""

    def test_collections_default_empty(self) -> None:
        """新建 Episode 的集合字段为空元组，下游可直接迭代。"""
        episode = Episode(
            episode_id="e1",
            task_id="t1",
            agent_id="a1",
            status=EpisodeStatus.RECORDING,
            created_at=NOW,
            updated_at=NOW,
        )
        assert episode.streams == ()
        assert episode.key_frames == ()
        assert episode.segments == ()
        assert episode.quality is None

    def test_rejects_negative_size(self) -> None:
        """文件大小不得为负。"""
        with pytest.raises(ValidationError):
            Episode(
                episode_id="e1",
                task_id="t1",
                agent_id="a1",
                status=EpisodeStatus.UPLOADED,
                size_bytes=-1,
                created_at=NOW,
                updated_at=NOW,
            )


@pytest.mark.unit
class TestWebSocketProtocol:
    """WS 帧的判别式解析 —— Agent 与 Platform 靠 type 区分消息。"""

    def test_protocol_constants_are_consistent(self) -> None:
        """超时必须大于心跳间隔，否则刚发心跳就被判离线。"""
        assert HEARTBEAT_TIMEOUT_SECONDS > HEARTBEAT_INTERVAL_SECONDS
        assert WS_PROTOCOL_VERSION == "1.0"

    def test_parses_register_frame(self) -> None:
        """注册帧按 type 解析到正确类型。"""
        frame = UPSTREAM_ADAPTER.validate_python(
            {
                "type": "up.register",
                "agent_id": "a1",
                "hostname": "pc-01",
                "version": "0.1.0",
                "protocol_version": WS_PROTOCOL_VERSION,
            }
        )
        assert isinstance(frame, RegisterFrame)
        assert frame.agent_id == "a1"

    def test_parses_heartbeat_frame(self) -> None:
        """心跳帧带嵌套 payload。"""
        frame = UPSTREAM_ADAPTER.validate_python(
            {
                "type": "up.heartbeat",
                "payload": {
                    "agent_id": "a1",
                    "version": "0.1.0",
                    "reported_at": NOW.isoformat(),
                    "disk_free_bytes": 1024,
                },
            }
        )
        assert isinstance(frame, HeartbeatFrame)
        assert frame.payload.agent_id == "a1"

    def test_parses_downstream_task_push(self) -> None:
        """下行任务推送解析成功。"""
        frame = DOWNSTREAM_ADAPTER.validate_python(
            {
                "type": "down.task_push",
                "message_id": "m1",
                "payload": {
                    "task_id": "t1",
                    "task_name": "厨房抓取",
                    "requirement": {
                        "robot_model": "rm-1",
                        "scene": "kitchen",
                        "required_topics": ["/camera/front"],
                        "min_duration_ms": 1000,
                        "max_duration_ms": 60000,
                        "target_episode_count": 10,
                    },
                    "pushed_at": NOW.isoformat(),
                },
            }
        )
        assert isinstance(frame, TaskPushFrame)
        assert frame.payload.requirement.scene == "kitchen"

    def test_upstream_rejects_downstream_frame(self) -> None:
        """方向不可混用：下行帧不能当上行解析。"""
        with pytest.raises(ValidationError):
            UPSTREAM_ADAPTER.validate_python(
                {"type": "down.task_cancel", "message_id": "m1", "task_id": "t1"}
            )

    def test_downstream_rejects_upstream_frame(self) -> None:
        """上行帧不能当下行解析。"""
        with pytest.raises(ValidationError):
            DOWNSTREAM_ADAPTER.validate_python({"type": "up.ack", "message_id": "m1"})

    def test_rejects_unknown_message_type(self) -> None:
        """未知 type 被拒。"""
        with pytest.raises(ValidationError):
            UPSTREAM_ADAPTER.validate_python({"type": "up.telepathy", "agent_id": "a1"})

    def test_all_message_types_prefixed_by_direction(self) -> None:
        """每个消息类型都有方向前缀，避免误用。

        三个方向：``up.`` Agent→Platform，``down.`` Platform→Agent，
        ``console.`` Platform→浏览器。
        """
        for member in MessageType:
            assert member.value.startswith(("up.", "down.", "console."))

    def test_console_agent_status_round_trip(self) -> None:
        """Agent 上下线帧能按 type 判别解析回来。"""
        original = ConsoleAgentStatusFrame(agent_id="a1", online=True, hostname="pc-01", at=NOW)
        parsed = CONSOLE_ADAPTER.validate_json(original.model_dump_json())
        assert parsed == original

    def test_console_upload_progress_round_trip(self) -> None:
        """上传进度帧能按 type 判别解析回来。"""
        original = ConsoleUploadProgressFrame(
            episode_id="e1",
            agent_id="a1",
            uploaded_parts=5,
            total_parts=10,
            percent=50.0,
        )
        parsed = CONSOLE_ADAPTER.validate_json(original.model_dump_json())
        assert parsed == original

    def test_console_frames_reject_out_of_range_percent(self) -> None:
        """百分比越界要被挡住。"""
        with pytest.raises(ValidationError):
            ConsoleUploadProgressFrame(
                episode_id="e1",
                agent_id="a1",
                uploaded_parts=1,
                total_parts=1,
                percent=101.0,
            )

    def test_console_progress_rejects_zero_total_parts(self) -> None:
        """总分片数为 0 会让百分比除零，契约层直接挡住。"""
        with pytest.raises(ValidationError):
            ConsoleUploadProgressFrame(
                episode_id="e1",
                agent_id="a1",
                uploaded_parts=0,
                total_parts=0,
                percent=0.0,
            )

    def test_round_trip_serialization(self) -> None:
        """序列化后能原样解析回来。"""
        original = RegisterFrame(
            agent_id="a1", hostname="pc-01", version="0.1.0", protocol_version="1.0"
        )
        parsed = UPSTREAM_ADAPTER.validate_json(original.model_dump_json())
        assert parsed == original

    def test_episode_status_frame_carries_status(self) -> None:
        """Agent 上报的状态帧携带 EpisodeStatus 枚举。"""
        frame = UPSTREAM_ADAPTER.validate_python(
            {
                "type": "up.episode_status",
                "episode_id": "e1",
                "status": "uploading",
                "reported_at": NOW.isoformat(),
            }
        )
        assert frame.status is EpisodeStatus.UPLOADING  # type: ignore[union-attr]


@pytest.mark.unit
class TestHeartbeatBounds:
    """心跳字段边界。"""

    def test_rejects_negative_disk(self) -> None:
        """磁盘余量不得为负。"""
        with pytest.raises(ValidationError):
            AgentHeartbeat(agent_id="a1", version="0.1.0", reported_at=NOW, disk_free_bytes=-1)

    def test_rejects_cpu_over_100(self) -> None:
        """CPU 占用不得超过 100。"""
        with pytest.raises(ValidationError):
            AgentHeartbeat(
                agent_id="a1",
                version="0.1.0",
                reported_at=NOW,
                disk_free_bytes=1,
                cpu_percent=101,
            )
