"""队列巡检：拓扑取自契约，深度数得准。

运维页靠这个判断「事件有没有被消费」，所以两件事不能错：队列清单与绑定必须跟着契约走
（新增事件时不能漏），文件队列的深度必须只数真正待消费的文件。

RabbitMQ 分支需要真 broker，由 `make rabbit-paths` 那组集成测试覆盖，这里只测 file 后端
与契约一致性。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdh_contract.enums import JobType
from rdh_contract.events import EVENT_REGISTRY, EXCHANGE_DLX, EXCHANGE_MAIN, routing_keys_for_queue

from app.services.queue_inspector import inspect_file_queues

pytestmark = pytest.mark.unit


def write_event(queue_dir: Path, queue: str, name: str) -> None:
    """在某队列目录下放一条消息。"""
    target = queue_dir / queue
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(json.dumps({"routing_key": "x"}), encoding="utf-8")


class TestTopologyFollowsContract:
    """队列清单与绑定不得硬编码。"""

    def test_every_job_type_is_listed(self, tmp_path: Path) -> None:
        """4 类 worker 队列全在 —— 契约加 JobType 时这条会红。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=tmp_path / "dlq")
        assert {q.queue for q in snapshot.queues} == {job.value for job in JobType}

    def test_routing_keys_come_from_contract(self, tmp_path: Path) -> None:
        """每个队列的绑定与 routing_keys_for_queue() 一致。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=tmp_path / "dlq")
        for depth in snapshot.queues:
            expected = routing_keys_for_queue(JobType(depth.queue))
            assert depth.routing_keys == expected

    def test_every_registered_event_is_covered(self, tmp_path: Path) -> None:
        """所有注册事件都被某个队列订阅 —— 否则事件发出去无人消费。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=tmp_path / "dlq")
        covered = {key for depth in snapshot.queues for key in depth.routing_keys}
        assert covered == set(EVENT_REGISTRY)

    def test_exchanges_come_from_contract(self, tmp_path: Path) -> None:
        """exchange 名取自契约，运维页显示的就是真实拓扑。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=tmp_path / "dlq")
        assert snapshot.exchange == EXCHANGE_MAIN
        assert snapshot.dlx == EXCHANGE_DLX


class TestFileBackendDepth:
    """文件队列的深度统计。"""

    def test_counts_pending_messages(self, tmp_path: Path) -> None:
        """按队列分别计数，不串。"""
        queue_dir = tmp_path / "q"
        write_event(queue_dir, "ingest", "1-a.json")
        write_event(queue_dir, "ingest", "2-b.json")
        write_event(queue_dir, "tool", "3-c.json")

        snapshot = inspect_file_queues(queue_dir=queue_dir, dlq_dir=tmp_path / "dlq")
        depths = {q.queue: q.pending for q in snapshot.queues}
        assert depths["ingest"] == 2
        assert depths["tool"] == 1
        assert depths["notify"] == 0

    def test_skips_partial_writes(self, tmp_path: Path) -> None:
        """写入中的临时文件（`.` 前缀）不算待消费。

        发布方是「临时文件 + 原子 rename」，把临时文件算进去会报出虚高的积压。
        """
        queue_dir = tmp_path / "q"
        write_event(queue_dir, "ingest", "1-real.json")
        write_event(queue_dir, "ingest", ".2-writing.json.tmp")

        snapshot = inspect_file_queues(queue_dir=queue_dir, dlq_dir=tmp_path / "dlq")
        depths = {q.queue: q.pending for q in snapshot.queues}
        assert depths["ingest"] == 1

    def test_missing_dir_is_zero_not_error(self, tmp_path: Path) -> None:
        """队列目录还不存在时算 0 —— 没跑过 demo 是正常状态，不是故障。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "nope", dlq_dir=tmp_path / "nada")
        assert all(q.pending == 0 for q in snapshot.queues)
        assert all(q.reachable for q in snapshot.queues)
        assert snapshot.error is None

    def test_dlq_sums_across_queues(self, tmp_path: Path) -> None:
        """死信按队列分目录，巡检给的是总数。"""
        dlq_dir = tmp_path / "dlq"
        write_event(dlq_dir, "ingest", "1-bad.json")
        write_event(dlq_dir, "tool", "2-bad.json")
        write_event(dlq_dir, "tool", "3-bad.json")

        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=dlq_dir)
        assert snapshot.dlq_count == 3

    def test_file_backend_has_no_broker_label(self, tmp_path: Path) -> None:
        """file 后端没有 broker 地址 —— 前端据此决定显示什么。"""
        snapshot = inspect_file_queues(queue_dir=tmp_path / "q", dlq_dir=tmp_path / "dlq")
        assert snapshot.backend == "file"
        assert snapshot.broker is None
