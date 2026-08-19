"""事件注册表一致性测试。

Platform 按此表发布、Scheduler 按此表消费。表与 payload 模型不一致会导致运行期反序列化失败，
routing_key 命名混乱会导致 topic 通配订阅意外匹配。
"""

import re

import pytest

from rdh_contract.enums import JobType
from rdh_contract.events import (
    EVENT_MODELS,
    EVENT_REGISTRY,
    EXCHANGE_DLX,
    EXCHANGE_MAIN,
    EventEnvelope,
    UnknownEventError,
    get_model,
    get_spec,
    routing_keys_for_queue,
)

# routing_key 命名规范：<domain>.<past_tense_verb>，全小写，下划线分词
ROUTING_KEY_PATTERN = re.compile(r"^[a-z]+(?:_[a-z]+)*\.[a-z]+(?:_[a-z]+)*$")


@pytest.mark.unit
class TestRegistryConsistency:
    """注册表与模型表必须严格对应。"""

    def test_registry_and_models_cover_same_keys(self) -> None:
        """两张表的 routing_key 集合必须一致，否则查得到规格却查不到模型。"""
        assert set(EVENT_REGISTRY) == set(EVENT_MODELS)

    def test_spec_routing_key_matches_dict_key(self) -> None:
        """规格内的 routing_key 与字典 key 一致，防止复制粘贴出错。"""
        for key, spec in EVENT_REGISTRY.items():
            assert spec.routing_key == key

    def test_model_name_matches_actual_model(self) -> None:
        """``EventSpec.model_name`` 与 :data:`EVENT_MODELS` 的实际类名一致。"""
        for key, spec in EVENT_REGISTRY.items():
            assert spec.model_name == EVENT_MODELS[key].__name__

    def test_registry_is_not_empty(self) -> None:
        """注册表非空（防止导入顺序问题导致静默清空）。"""
        assert EVENT_REGISTRY


@pytest.mark.unit
class TestRoutingKeyConventions:
    """routing_key 命名与唯一性。"""

    def test_naming_convention(self) -> None:
        """符合 ``<domain>.<past_tense_verb>`` 规范。"""
        for key in EVENT_REGISTRY:
            assert ROUTING_KEY_PATTERN.match(key), f"routing_key 不符合命名规范：{key}"

    def test_no_duplicate_keys_case_insensitive(self) -> None:
        """忽略大小写后仍无重复（RabbitMQ routing_key 大小写敏感，混用是坑）。"""
        lowered = [k.lower() for k in EVENT_REGISTRY]
        assert len(lowered) == len(set(lowered))

    def test_domain_prefix_is_known(self) -> None:
        """domain 前缀受控，避免事件命名空间失控。"""
        allowed_domains = {"episode", "annotation", "dataset"}
        for key in EVENT_REGISTRY:
            domain = key.split(".", 1)[0]
            assert domain in allowed_domains, f"未知 domain 前缀：{domain}（来自 {key}）"

    def test_no_key_is_prefix_of_another(self) -> None:
        """任一 key 不得是另一个的前缀，避免 topic 通配订阅意外匹配。"""
        keys = sorted(EVENT_REGISTRY)
        for i, key in enumerate(keys):
            for other in keys[i + 1 :]:
                assert not other.startswith(f"{key}."), f"{key} 是 {other} 的前缀"


@pytest.mark.unit
class TestPayloadModels:
    """payload 模型约束。"""

    def test_all_payloads_extend_envelope(self) -> None:
        """所有事件都带 ``event_id`` / ``occurred_at``，消费方才能做幂等与延迟监控。"""
        for key, model in EVENT_MODELS.items():
            assert issubclass(model, EventEnvelope), f"{key} 的模型未继承 EventEnvelope"

    def test_payloads_are_frozen(self) -> None:
        """事件不可变，防止消费方在处理链中改写原始消息。"""
        for key, model in EVENT_MODELS.items():
            assert model.model_config.get("frozen") is True, f"{key} 的模型可变"

    def test_payloads_forbid_extra_fields(self) -> None:
        """拒绝未声明字段，让契约漂移在反序列化时立即暴露。"""
        for key, model in EVENT_MODELS.items():
            assert model.model_config.get("extra") == "forbid", f"{key} 的模型允许额外字段"

    def test_payloads_are_json_serializable(self) -> None:
        """每个模型都能生成 JSON Schema（导出脚本依赖此能力）。"""
        for key, model in EVENT_MODELS.items():
            schema = model.model_json_schema(mode="serialization")
            assert schema["properties"], f"{key} 的 schema 无字段"

    def test_every_payload_has_episode_or_dataset_ref(self) -> None:
        """每个事件都能定位到业务对象，否则消费方不知道该处理什么。"""
        for key, model in EVENT_MODELS.items():
            fields = set(model.model_fields)
            assert fields & {"episode_id", "dataset_id"}, f"{key} 无业务对象引用"


@pytest.mark.unit
class TestQueueRouting:
    """消费队列映射。"""

    def test_every_event_maps_to_a_worker_queue(self) -> None:
        """每个事件都有消费方，否则消息发出去无人处理。"""
        for key, spec in EVENT_REGISTRY.items():
            assert isinstance(spec.consumer_queue, JobType), f"{key} 的消费队列非法"

    def test_routing_keys_for_queue_partitions_registry(self) -> None:
        """按队列分组后并集等于全集，且各组互不重叠。"""
        collected: list[str] = []
        for queue in JobType:
            collected.extend(routing_keys_for_queue(queue))
        assert sorted(collected) == sorted(EVENT_REGISTRY)
        assert len(collected) == len(set(collected))

    def test_routing_keys_for_queue_is_sorted(self) -> None:
        """返回值有序，保证 Scheduler 订阅顺序可复现。"""
        for queue in JobType:
            keys = routing_keys_for_queue(queue)
            assert list(keys) == sorted(keys)

    def test_ingest_queue_handles_upload_event(self) -> None:
        """``episode.uploaded`` 由 ingest-worker 消费（架构文档第三节）。"""
        assert "episode.uploaded" in routing_keys_for_queue(JobType.INGEST)

    def test_dataset_build_handled_by_tool_worker(self) -> None:
        """训练集构建由 tool-worker 消费。"""
        assert "dataset.build_requested" in routing_keys_for_queue(JobType.TOOL)


@pytest.mark.unit
class TestExchangesAndRetries:
    """exchange 与重试策略。"""

    def test_all_events_use_main_exchange(self) -> None:
        """当前所有业务事件走主 exchange。"""
        for key, spec in EVENT_REGISTRY.items():
            assert spec.exchange == EXCHANGE_MAIN, f"{key} 的 exchange 异常"

    def test_dlx_differs_from_main(self) -> None:
        """死信 exchange 与主 exchange 不同名，否则死信会被重新投递成正常消息。"""
        assert EXCHANGE_DLX != EXCHANGE_MAIN

    def test_retry_counts_are_sane(self) -> None:
        """重试次数在合理范围，避免无限重试拖垮 worker。"""
        for key, spec in EVENT_REGISTRY.items():
            assert 0 <= spec.max_retries <= 5, f"{key} 的 max_retries 异常：{spec.max_retries}"


@pytest.mark.unit
class TestLookupApi:
    """查询 API 行为。"""

    def test_get_spec_returns_registered_spec(self) -> None:
        """按 key 取到对应规格。"""
        assert get_spec("episode.uploaded").routing_key == "episode.uploaded"

    def test_get_model_returns_registered_model(self) -> None:
        """按 key 取到对应模型。"""
        assert get_model("episode.uploaded") is EVENT_MODELS["episode.uploaded"]

    def test_get_spec_raises_on_unknown(self) -> None:
        """未注册 key 抛 :class:`UnknownEventError`，并列出已注册项。"""
        with pytest.raises(UnknownEventError) as exc_info:
            get_spec("episode.exploded")
        assert "episode.uploaded" in str(exc_info.value)

    def test_get_model_raises_on_unknown(self) -> None:
        """未注册 key 取模型同样抛错。"""
        with pytest.raises(UnknownEventError):
            get_model("nope.happened")

    def test_unknown_event_error_is_key_error(self) -> None:
        """继承 KeyError，便于消费方按标准异常处理。"""
        assert issubclass(UnknownEventError, KeyError)
