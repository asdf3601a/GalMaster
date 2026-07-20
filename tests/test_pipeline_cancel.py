"""Cancel / abort behaviour for TranslationPipeline Process stage."""

from __future__ import annotations

import threading
import time
from collections import deque

import pytest
from PIL import Image

from app.config import AppConfig
from app.i18n import tr
from app.pipeline import (
    PipelineJob,
    PipelineResult,
    TranslationPipeline,
    _JobCancelled,
    _Worker,
)


def test_run_with_deadline_aborts_quickly() -> None:
    """Abort during LLM wait must not block for the full wall timeout."""
    worker = _Worker()
    gen = worker._generation

    def slow() -> str:
        time.sleep(5.0)
        return "ok"

    def abort_soon() -> None:
        time.sleep(0.05)
        worker.request_abort()

    threading.Thread(target=abort_soon, daemon=True).start()
    t0 = time.monotonic()
    try:
        with pytest.raises(_JobCancelled):
            worker._run_with_deadline(slow, timeout_s=30.0, gen=gen)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"abort took too long: {elapsed:.2f}s"
    finally:
        worker._llm_pool.shutdown(wait=False, cancel_futures=True)


def test_run_with_deadline_generation_invalidate() -> None:
    """Generation bump aborts wait without waiting on the pool task."""
    worker = _Worker()
    gen = worker._generation
    worker._generation += 1

    def never() -> str:
        time.sleep(10.0)
        return "ok"

    t0 = time.monotonic()
    try:
        with pytest.raises(_JobCancelled):
            worker._run_with_deadline(never, timeout_s=30.0, gen=gen)
        assert time.monotonic() - t0 < 1.0
    finally:
        worker._llm_pool.shutdown(wait=False, cancel_futures=True)


def test_cancel_clears_queue_and_aborts(qapp) -> None:
    pipe = TranslationPipeline()
    try:
        pipe._busy = True
        cfg = AppConfig()
        img = Image.new("RGB", (8, 8), color=(0, 0, 0))
        pipe._queue = deque(
            [
                PipelineJob(cfg=cfg, image=img, force=False),
                PipelineJob(cfg=cfg, image=img, force=True),
            ]
        )
        assert pipe.queue_depth == 2
        pipe.cancel()
        assert pipe.queue_depth == 0
        aborted = False
        for _ in range(50):
            qapp.processEvents()
            if pipe._worker._abort:
                aborted = True
                break
            time.sleep(0.02)
        assert aborted, "request_abort should reach worker via queued connection"
    finally:
        pipe.shutdown()


def test_cancelled_result_is_status_only() -> None:
    """Cancel must be skipped status, not a soft LLM error wipe."""
    r = PipelineResult("", "", skipped=True, status_message=tr("pipe.cancelled"))
    assert r.skipped is True
    assert r.status_message == tr("pipe.cancelled")
    assert not r.error
    assert not r.source_text


def test_emit_cancelled_clears_sticky_abort() -> None:
    """Regression: cancel must not poison all subsequent OCR jobs."""
    worker = _Worker()
    worker.request_abort()
    assert worker._abort is True
    worker._emit_cancelled()
    assert worker._abort is False


def test_cancel_when_idle_does_not_abort(qapp) -> None:
    """Stop-monitor while idle should not cancel the next translate."""
    pipe = TranslationPipeline()
    try:
        assert pipe.busy is False
        pipe.cancel()
        for _ in range(20):
            qapp.processEvents()
            time.sleep(0.01)
        assert pipe._worker._abort is False
    finally:
        pipe.shutdown()
