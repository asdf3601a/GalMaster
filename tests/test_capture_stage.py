"""Unit tests for CaptureStage (no Qt / no screenshot)."""

from __future__ import annotations

from app.session.capture_stage import CapturePhase, CaptureStage


def test_idle_request_starts() -> None:
    s = CaptureStage()
    assert s.request(force=False, buffer_cap=3) == "start"
    assert s.phase is CapturePhase.CAPTURING
    assert s.pending_force is False


def test_force_request_clears_deferred_autos() -> None:
    s = CaptureStage()
    s.request(force=False, buffer_cap=3)
    s.finish()
    # Simulate leftover deferred (should not happen after clean finish+pump, but)
    s.deferred_auto = 2
    assert s.request(force=True, buffer_cap=3) == "start"
    assert s.deferred_auto == 0
    assert s.pending_force is True


def test_deferred_auto_while_capturing_respects_cap() -> None:
    s = CaptureStage()
    assert s.request(force=False, buffer_cap=2) == "start"
    assert s.request(force=False, buffer_cap=2) == "deferred"
    assert s.request(force=False, buffer_cap=2) == "deferred"
    assert s.request(force=False, buffer_cap=2) == "deferred"  # over cap ignored
    assert s.deferred_auto == 2


def test_force_while_capturing_sets_recapture() -> None:
    s = CaptureStage()
    s.request(force=False, buffer_cap=3)
    assert s.request(force=True, buffer_cap=3) == "deferred"
    assert s.pending_force_recapture is True


def test_finish_and_pump_force_prefers_over_auto() -> None:
    s = CaptureStage()
    s.request(force=False, buffer_cap=3)
    s.request(force=False, buffer_cap=3)  # deferred_auto = 1
    s.request(force=True, buffer_cap=3)  # force recapture
    force, _ = s.finish()
    assert force is False  # current job was auto
    assert s.phase is CapturePhase.IDLE
    assert s.pump() == "force"
    assert s.phase is CapturePhase.CAPTURING
    assert s.pending_force is True
    assert s.deferred_auto == 0


def test_pump_auto_after_finish() -> None:
    s = CaptureStage()
    s.request(force=False, buffer_cap=3)
    s.request(force=False, buffer_cap=3)
    s.request(force=False, buffer_cap=3)
    assert s.deferred_auto == 2
    s.finish()
    assert s.pump() == "auto"
    assert s.pending_force is False
    s.finish()
    assert s.pump() == "auto"
    s.finish()
    assert s.pump() is None


def test_cloak_restore() -> None:
    s = CaptureStage()
    s.begin_grab(force=True, overlay_was_visible=True, overlay_opacity=0.88)
    s.mark_cloaked()
    op = s.take_cloak_restore()
    assert op == 0.88
    assert s.overlay_cloaked is False
    assert s.take_cloak_restore() is None


def test_reset_deferred() -> None:
    s = CaptureStage()
    s.deferred_auto = 3
    s.pending_force_recapture = True
    s.reset_deferred()
    assert s.deferred_auto == 0
    assert s.pending_force_recapture is False
