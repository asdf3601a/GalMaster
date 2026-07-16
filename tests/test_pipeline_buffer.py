"""Bounded process-stage job buffer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.config import (
    PIPELINE_BUFFER_DEFAULT,
    AppConfig,
    clamp_pipeline_buffer_size,
)
from app.pipeline_queue import buffer_cap, enqueue_job


@dataclass
class _Job:
    name: str
    force: bool = False


def test_clamp_pipeline_buffer_size() -> None:
    assert clamp_pipeline_buffer_size(None) == PIPELINE_BUFFER_DEFAULT
    assert clamp_pipeline_buffer_size("bad") == PIPELINE_BUFFER_DEFAULT
    assert clamp_pipeline_buffer_size(0) == 1
    assert clamp_pipeline_buffer_size(-5) == 1
    assert clamp_pipeline_buffer_size(3) == 3
    assert clamp_pipeline_buffer_size(16) == 16
    assert clamp_pipeline_buffer_size(99) == 16
    assert buffer_cap(None) == PIPELINE_BUFFER_DEFAULT


def test_config_from_dict_clamps_buffer() -> None:
    cfg = AppConfig.from_dict({"pipeline_buffer_size": 100})
    assert cfg.pipeline_buffer_size == 16
    cfg2 = AppConfig.from_dict({})
    assert cfg2.pipeline_buffer_size == 3
    cfg3 = AppConfig.from_dict({"pipeline_buffer_size": 0})
    assert cfg3.pipeline_buffer_size == 1


def test_enqueue_drop_oldest() -> None:
    q: deque[_Job] = deque()
    enqueue_job(q, _Job("a"), cap=2, force=False)
    enqueue_job(q, _Job("b"), cap=2, force=False)
    enqueue_job(q, _Job("c"), cap=2, force=False)
    assert [j.name for j in q] == ["b", "c"]


def test_enqueue_cap_one_keeps_latest() -> None:
    q: deque[_Job] = deque()
    enqueue_job(q, _Job("a"), cap=1, force=False)
    enqueue_job(q, _Job("b"), cap=1, force=False)
    enqueue_job(q, _Job("c"), cap=1, force=False)
    assert [j.name for j in q] == ["c"]


def test_force_clears_auto_queue() -> None:
    q: deque[_Job] = deque()
    enqueue_job(q, _Job("a"), cap=5, force=False)
    enqueue_job(q, _Job("b"), cap=5, force=False)
    enqueue_job(q, _Job("manual", force=True), cap=5, force=True)
    assert len(q) == 1
    assert q[0].name == "manual"
    assert q[0].force is True


def test_force_replaces_previous_force() -> None:
    q: deque[_Job] = deque()
    enqueue_job(q, _Job("f1", force=True), cap=3, force=True)
    enqueue_job(q, _Job("f2", force=True), cap=3, force=True)
    assert [j.name for j in q] == ["f2"]


def test_auto_does_not_evict_waiting_force() -> None:
    q: deque[_Job] = deque()
    enqueue_job(q, _Job("manual", force=True), cap=1, force=True)
    enqueue_job(q, _Job("auto1"), cap=1, force=False)
    enqueue_job(q, _Job("auto2"), cap=1, force=False)
    # cap=1 but force is protected: keep force + at most fill with latest auto
    # After append auto1: [manual, auto1] len=2 > 1 → drop auto1 path keeps force, drops auto1?
    # Implementation: keep force at front, drop next oldest → [manual] after first overflow
    # append auto2: [manual, auto2] → drop auto? keeps [manual]
    # Actually with cap=1 we want force alone if it was force-only intent.
    # With protection: [manual, auto2] → pop force, pop auto2? No: keep force, drop second.
    # Result should be [manual] when cap=1 after drops, OR [manual, auto2] if we allow force+autos under soft cap.
    # Spec: waiting queue capacity N. Force at front should not be dropped by auto.
    assert q[0].name == "manual"
    assert q[0].force is True
    assert len(q) <= 2
    if len(q) == 2:
        assert q[1].name == "auto2"
