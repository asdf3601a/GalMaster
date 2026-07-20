"""RegionMonitor must stop cleanly (no hung QThread event loop)."""

from __future__ import annotations

import sys
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.capture.monitor import RegionMonitor
from app.config import AppConfig


@pytest.fixture()
def qapp():
    # Prefer QApplication so later widget tests in the same process keep working.
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_monitor_stop_exits_thread_quickly(qapp):
    """
    Regression: worker.run() used to return without QThread.quit(), so
    thread.wait() always timed out, zombies accumulated, and auto-monitor
    (and anything waiting on stop) hung.
    """
    mon = RegionMonitor()
    cfg = AppConfig(
        region_x=0,
        region_y=0,
        region_w=8,
        region_h=8,
        monitor_interval_ms=200,
        monitor_stable_ms=0,
        monitor_cooldown_ms=0,
    )
    mon.start(cfg)
    assert mon.is_running
    # Let the loop tick once (capture may fail — that's fine)
    time.sleep(0.35)
    t0 = time.monotonic()
    mon.stop(wait=0.2)
    elapsed = time.monotonic() - t0
    assert not mon.is_running
    # Must not block the UI on capture/OCR — short join only
    assert elapsed < 1.0, f"stop() took {elapsed:.2f}s (blocked too long)"
    # Restart should also work without zombie errors
    mon.start(cfg)
    assert mon.is_running
    mon.stop()
    assert not mon.is_running


def test_monitor_start_stop_start(qapp):
    mon = RegionMonitor()
    cfg = AppConfig(
        region_w=4, region_h=4, monitor_interval_ms=200, monitor_stable_ms=0
    )
    for _ in range(3):
        mon.start(cfg)
        assert mon.is_running
        time.sleep(0.15)
        mon.stop()
        assert not mon.is_running


def test_monitor_per_generation_stop_event(qapp):
    """start() must use a new Event so a late zombie cannot be revived by clear()."""
    mon = RegionMonitor()
    cfg = AppConfig(
        region_w=4, region_h=4, monitor_interval_ms=200, monitor_stable_ms=0
    )
    mon.start(cfg)
    ev1 = mon._stop
    mon.stop()
    mon.start(cfg)
    ev2 = mon._stop
    assert ev1 is not ev2
    assert ev1.is_set()
    assert not ev2.is_set()
    mon.stop()
