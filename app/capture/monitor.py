"""Auto-monitor OCR region for visual changes (+ optional stable-duration wait)."""

from __future__ import annotations

import threading
import time
from copy import deepcopy

import numpy as np
from PySide6.QtCore import QObject, Signal

from app.capture.screenshot import capture_region, image_to_gray_array, mean_abs_diff
from app.config import AppConfig
from app.i18n import tr


class RegionMonitor(QObject):
    """Background region change detector (daemon thread + Qt signals)."""

    region_changed = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._zombie: threading.Thread | None = None
        # Current generation's stop flag. Each start() creates a new Event so a
        # timed-out join cannot revive an abandoned loop when the next start clears it.
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._cfg: AppConfig | None = None
        self._last_gray: np.ndarray | None = None
        self._last_fire = 0.0
        self._interval_s = 0.6
        self._threshold = 0.04
        self._cooldown_s = 1.2
        self._wait_stable = True
        self._stable_s = 0.8
        self._pending_change = False
        self._stable_since: float | None = None
        self._idle_status_at = 0.0

    @property
    def is_running(self) -> bool:
        t = self._thread
        if t is not None and t.is_alive():
            return True
        z = self._zombie
        return z is not None and z.is_alive()

    def configure(self, cfg: AppConfig) -> None:
        with self._lock:
            self._cfg = deepcopy(cfg)
            self._apply_cfg_params(cfg)

    def update_config(self, cfg: AppConfig) -> None:
        self.configure(cfg)

    def _apply_cfg_params(self, cfg: AppConfig) -> None:
        interval_ms = int(getattr(cfg, "monitor_interval_ms", 600) or 0)
        if interval_ms <= 0:
            interval_ms = 600
        self._interval_s = max(0.2, interval_ms / 1000.0)
        self._threshold = max(0.0, float(cfg.monitor_diff_threshold))
        cooldown_ms = int(getattr(cfg, "monitor_cooldown_ms", 1200) or 0)
        self._cooldown_s = max(0.0, cooldown_ms / 1000.0)
        stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
        self._wait_stable = stable_ms > 0
        self._stable_s = max(0.0, stable_ms / 1000.0)

    def reset_baseline(self) -> None:
        with self._lock:
            self._last_gray = None
            self._last_fire = 0.0
            self._pending_change = False
            self._stable_since = None

    def _reclaim_zombie(self, timeout: float = 2.0) -> bool:
        z = self._zombie
        if z is None:
            return True
        if not z.is_alive():
            self._zombie = None
            return True
        z.join(timeout=timeout)
        if z.is_alive():
            return False
        self._zombie = None
        return True

    def start(self, cfg: AppConfig) -> None:
        if not self._reclaim_zombie(2.0):
            self.error.emit(tr("monitor.zombie"))
            return
        self.stop()
        if not self._reclaim_zombie(1.0):
            self.error.emit(tr("monitor.zombie"))
            return

        self.configure(cfg)
        self.reset_baseline()
        stop_ev = threading.Event()
        self._stop = stop_ev
        t = threading.Thread(
            target=self._run_loop,
            args=(stop_ev,),
            name="GalMaster-RegionMonitor",
            daemon=True,
        )
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        self._thread = None
        if t is not None and t.is_alive():
            # Capture can block briefly; join with a cap so UI never freezes long.
            t.join(timeout=4.0)
            if t.is_alive():
                # Do not clear the generation stop event; keep as zombie so
                # start() refuses until it dies (never share a cleared Event).
                self._zombie = t

    def _sleep_interruptible(self, stop_ev: threading.Event, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while not stop_ev.is_set() and time.monotonic() < end:
            time.sleep(min(0.05, end - time.monotonic()))

    def _fire_if_cooldown(
        self, stop_ev: threading.Event, cooldown_s: float, wait_stable: bool
    ) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_fire < cooldown_s:
                in_cooldown = True
            else:
                in_cooldown = False
                self._last_fire = now
                self._pending_change = False
                self._stable_since = None
        if in_cooldown:
            self.status.emit(tr("monitor.cooldown"))
            return
        if wait_stable:
            self.status.emit(tr("monitor.stable_fire"))
        else:
            self.status.emit(tr("monitor.change_fire"))
        if not stop_ev.is_set():
            self.region_changed.emit()

    def _run_loop(self, stop_ev: threading.Event) -> None:
        with self._lock:
            if self._wait_stable:
                mode = tr("monitor.mode_stable", ms=int(self._stable_s * 1000))
            else:
                mode = tr("monitor.mode_immediate")
        self.status.emit(tr("monitor.running", mode=mode))

        while not stop_ev.is_set():
            with self._lock:
                cfg = deepcopy(self._cfg) if self._cfg is not None else None
                if cfg is not None:
                    self._apply_cfg_params(cfg)
                wait_stable = self._wait_stable
                stable_s = self._stable_s
                interval_s = self._interval_s
                threshold = self._threshold
                cooldown_s = self._cooldown_s

            if cfg is None or not cfg.has_region:
                self._sleep_interruptible(stop_ev, 0.3)
                continue

            try:
                img = capture_region(
                    hwnd=cfg.bound_hwnd or None,
                    rel_x=cfg.region_x,
                    rel_y=cfg.region_y,
                    rel_w=cfg.region_w,
                    rel_h=cfg.region_h,
                    abs_x=int(getattr(cfg, "region_abs_x", 0) or 0),
                    abs_y=int(getattr(cfg, "region_abs_y", 0) or 0),
                    abs_w=int(getattr(cfg, "region_abs_w", 0) or 0),
                    abs_h=int(getattr(cfg, "region_abs_h", 0) or 0),
                )
                if stop_ev.is_set():
                    break
                gray = image_to_gray_array(img)

                status_msg: str | None = None
                should_fire = False
                with self._lock:
                    if stop_ev.is_set():
                        break
                    if self._last_gray is None:
                        self._last_gray = gray
                        status_msg = tr("monitor.baseline")
                    else:
                        diff = mean_abs_diff(self._last_gray, gray)
                        eff_threshold = max(float(threshold), 1e-6)
                        quiet = eff_threshold * 0.45
                        now = time.monotonic()

                        if not wait_stable:
                            if diff >= eff_threshold:
                                self._last_gray = gray
                                should_fire = True
                            else:
                                self._last_gray = gray * 0.2 + self._last_gray * 0.8
                                if now - self._idle_status_at > 8.0:
                                    self._idle_status_at = now
                                    status_msg = tr("monitor.idle_immediate")
                        elif diff >= eff_threshold:
                            self._pending_change = True
                            self._stable_since = None
                            self._last_gray = gray
                            status_msg = tr("monitor.waiting_stable")
                        elif self._pending_change:
                            if diff <= quiet:
                                if self._stable_since is None:
                                    self._stable_since = now
                                held = now - self._stable_since
                                need = stable_s
                                pct = (
                                    min(100, int(100 * held / need)) if need > 0 else 100
                                )
                                status_msg = tr(
                                    "monitor.stable_progress",
                                    held=f"{held * 1000:.0f}",
                                    need=f"{need * 1000:.0f}",
                                    pct=pct,
                                )
                                self._last_gray = gray
                                if held >= need:
                                    should_fire = True
                            else:
                                self._stable_since = None
                                self._last_gray = gray
                                status_msg = tr("monitor.still_changing")
                        else:
                            self._last_gray = gray * 0.2 + self._last_gray * 0.8
                            if now - self._idle_status_at > 8.0:
                                self._idle_status_at = now
                                status_msg = tr(
                                    "monitor.idle_stable",
                                    ms=int(stable_s * 1000),
                                )

                if status_msg is not None:
                    self.status.emit(status_msg)
                if should_fire and not stop_ev.is_set():
                    self._fire_if_cooldown(stop_ev, cooldown_s, wait_stable)
            except Exception as exc:
                if not stop_ev.is_set():
                    self.error.emit(str(exc))
            self._sleep_interruptible(stop_ev, interval_s)
