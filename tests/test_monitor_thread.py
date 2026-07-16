"""RegionMonitor must stop cleanly (no hung QThread event loop)."""

from __future__ import annotations

import sys
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication

from app.capture.monitor import RegionMonitor
from app.config import AppConfig


@pytest.fixture()
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)
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
    mon.stop()
    elapsed = time.monotonic() - t0
    assert not mon.is_running
    # Cooperative stop should finish well under the old 8s hang
    assert elapsed < 3.0, f"stop() took {elapsed:.2f}s (thread likely hung)"
    # Restart should also work without zombie errors
    mon.start(cfg)
    assert mon.is_running
    mon.stop()
    assert not mon.is_running


def test_monitor_start_stop_start(qapp):
    mon = RegionMonitor()
    cfg = AppConfig(region_w=4, region_h=4, monitor_interval_ms=200, monitor_stable_ms=0)
    for _ in range(3):
        mon.start(cfg)
        assert mon.is_running
        time.sleep(0.15)
        mon.stop()
        assert not mon.is_running
