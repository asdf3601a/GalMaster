"""Auto-monitor OCR region for visual changes (+ optional stable-duration wait)."""

from __future__ import annotations

import threading
import time
from copy import deepcopy

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from app.capture.screenshot import capture_region, image_to_gray_array, mean_abs_diff
from app.config import AppConfig


class _MonitorWorker(QObject):
    changed = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._running = False
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

    def configure(self, cfg: AppConfig) -> None:
        with self._lock:
            self._cfg = deepcopy(cfg)
            interval_ms = int(getattr(cfg, "monitor_interval_ms", 600) or 0)
            # 0 in UI means "use default"; floor 200ms when set
            if interval_ms <= 0:
                interval_ms = 600
            self._interval_s = max(0.2, interval_ms / 1000.0)
            self._threshold = max(0.0, float(cfg.monitor_diff_threshold))
            self._cooldown_s = max(0.3, cfg.monitor_cooldown_ms / 1000.0)
            stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
            # 0 = change triggers OCR immediately (no stable wait)
            self._wait_stable = stable_ms > 0
            self._stable_s = max(0.0, stable_ms / 1000.0)

    def reset_baseline(self) -> None:
        with self._lock:
            self._last_gray = None
            self._last_fire = 0.0
            self._pending_change = False
            self._stable_since = None

    def stop(self) -> None:
        self._running = False

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in short slices so stop() is responsive."""
        end = time.monotonic() + max(0.0, seconds)
        while self._running and time.monotonic() < end:
            time.sleep(min(0.05, end - time.monotonic()))

    def _fire_if_cooldown(self) -> None:
        now = time.monotonic()
        if now - self._last_fire < self._cooldown_s:
            self.status.emit("冷卻中，稍候再觸發…")
            return
        self._last_fire = now
        self._pending_change = False
        self._stable_since = None
        if self._wait_stable:
            self.status.emit("畫面已穩定，開始處理…")
        else:
            self.status.emit("偵測到變化，開始處理…")
        self.changed.emit()

    def run(self) -> None:
        self._running = True
        with self._lock:
            mode = (
                f"穩定 {int(self._stable_s * 1000)}ms 後辨識"
                if self._wait_stable
                else "變化即辨識"
            )
        self.status.emit(f"監控中（{mode}）…")
        while self._running:
            with self._lock:
                cfg = deepcopy(self._cfg) if self._cfg is not None else None
                wait_stable = self._wait_stable
                stable_s = self._stable_s
                interval_s = self._interval_s
                threshold = self._threshold
                cooldown_s = self._cooldown_s
            if cfg is None or not cfg.has_region:
                self._sleep_interruptible(0.3)
                continue
            try:
                with self._lock:
                    stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
                    wait_stable = stable_ms > 0
                    stable_s = max(0.0, stable_ms / 1000.0)
                    interval_ms = int(getattr(cfg, "monitor_interval_ms", 600) or 0)
                    if interval_ms <= 0:
                        interval_ms = 600
                    interval_s = max(0.2, interval_ms / 1000.0)
                    threshold = max(0.0, float(cfg.monitor_diff_threshold))
                    cooldown_s = max(0.3, cfg.monitor_cooldown_ms / 1000.0)
                    self._wait_stable = wait_stable
                    self._stable_s = stable_s
                    self._interval_s = interval_s
                    self._threshold = threshold
                    self._cooldown_s = cooldown_s

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
                if not self._running:
                    break
                gray = image_to_gray_array(img)
                with self._lock:
                    if not self._running:
                        break
                    if self._last_gray is None:
                        self._last_gray = gray
                        self.status.emit("監控中：已建立基準畫面")
                    else:
                        diff = mean_abs_diff(self._last_gray, gray)
                        # 0.0 means "maximum sensitivity" but must still require a
                        # real positive change — never treat every frame as changed.
                        eff_threshold = max(float(threshold), 1e-6)
                        quiet = eff_threshold * 0.45
                        now = time.monotonic()
                        self._cooldown_s = cooldown_s

                        if not wait_stable:
                            if diff >= eff_threshold:
                                self._last_gray = gray
                                self._fire_if_cooldown()
                            else:
                                self._last_gray = gray * 0.2 + self._last_gray * 0.8
                                if now - self._idle_status_at > 8.0:
                                    self._idle_status_at = now
                                    self.status.emit("監控中（變化即辨識）…")
                        elif diff >= eff_threshold:
                            self._pending_change = True
                            self._stable_since = None
                            self._last_gray = gray
                            self.status.emit("偵測到變化，等待畫面穩定…")
                        elif self._pending_change:
                            if diff <= quiet:
                                if self._stable_since is None:
                                    self._stable_since = now
                                held = now - self._stable_since
                                need = stable_s
                                pct = min(100, int(100 * held / need)) if need > 0 else 100
                                self.status.emit(
                                    f"等待畫面穩定… {held * 1000:.0f}/{need * 1000:.0f} ms（{pct}%）"
                                )
                                self._last_gray = gray
                                if held >= need:
                                    self._fire_if_cooldown()
                            else:
                                self._stable_since = None
                                self._last_gray = gray
                                self.status.emit("畫面仍在變化，繼續等待穩定…")
                        else:
                            self._last_gray = gray * 0.2 + self._last_gray * 0.8
                            if now - self._idle_status_at > 8.0:
                                self._idle_status_at = now
                                self.status.emit(
                                    f"監控中（穩定 {int(stable_s * 1000)}ms 後辨識）…"
                                )
            except Exception as exc:
                if self._running:
                    self.error.emit(str(exc))
            self._sleep_interruptible(interval_s)


class RegionMonitor(QObject):
    """Background region change detector."""

    region_changed = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _MonitorWorker | None = None
        self._zombie_thread: QThread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _wait_zombie(self, timeout_ms: int = 15000) -> bool:
        z = self._zombie_thread
        if z is None:
            return True
        if not z.isRunning():
            self._zombie_thread = None
            return True
        if z.wait(timeout_ms):
            self._zombie_thread = None
            return True
        return False

    def start(self, cfg: AppConfig) -> None:
        if not self._wait_zombie(15000):
            self.error.emit("先前監控執行緒仍在執行，請稍後再試")
            return
        self.stop()
        self._thread = QThread(self)
        self._worker = _MonitorWorker()
        self._worker.configure(cfg)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.changed.connect(self.region_changed)
        self._worker.error.connect(self.error)
        self._worker.status.connect(self.status)
        self._thread.start()

    def update_config(self, cfg: AppConfig) -> None:
        if self._worker:
            self._worker.configure(cfg)

    def reset_baseline(self) -> None:
        if self._worker:
            self._worker.reset_baseline()

    def stop(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._thread:
            # run() is a blocking loop — quit() is a no-op; wait for cooperative stop
            if not self._thread.wait(8000):
                # Do not abandon a running thread and spawn another; keep as zombie
                try:
                    self._worker.changed.disconnect()
                    self._worker.error.disconnect()
                    self._worker.status.disconnect()
                except Exception:
                    pass
                self._zombie_thread = self._thread
                try:
                    self._zombie_thread.finished.connect(self._zombie_thread.deleteLater)
                except Exception:
                    pass
            self._thread = None
            self._worker = None
