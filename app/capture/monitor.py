"""Auto-monitor OCR region for visual changes (+ optional stable-duration wait)."""

from __future__ import annotations

import time

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
        self._cfg = cfg
        self._interval_s = max(0.2, cfg.monitor_interval_ms / 1000.0)
        self._threshold = cfg.monitor_diff_threshold
        self._cooldown_s = max(0.3, cfg.monitor_cooldown_ms / 1000.0)
        self._wait_stable = bool(getattr(cfg, "monitor_wait_stable", True))
        self._stable_s = max(0.1, int(getattr(cfg, "monitor_stable_ms", 800) or 800) / 1000.0)

    def reset_baseline(self) -> None:
        self._last_gray = None
        self._last_fire = 0.0
        self._pending_change = False
        self._stable_since = None

    def stop(self) -> None:
        self._running = False

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
        mode = (
            f"穩定 {int(self._stable_s * 1000)}ms 後辨識"
            if self._wait_stable
            else "變化即辨識"
        )
        self.status.emit(f"監控中（{mode}）…")
        while self._running:
            cfg = self._cfg
            if cfg is None or not cfg.has_region:
                time.sleep(0.3)
                continue
            try:
                self._wait_stable = bool(getattr(cfg, "monitor_wait_stable", True))
                self._stable_s = max(
                    0.1, int(getattr(cfg, "monitor_stable_ms", 800) or 800) / 1000.0
                )
                self._interval_s = max(0.2, cfg.monitor_interval_ms / 1000.0)
                self._threshold = cfg.monitor_diff_threshold
                self._cooldown_s = max(0.3, cfg.monitor_cooldown_ms / 1000.0)

                img = capture_region(
                    hwnd=cfg.bound_hwnd or None,
                    rel_x=cfg.region_x,
                    rel_y=cfg.region_y,
                    rel_w=cfg.region_w,
                    rel_h=cfg.region_h,
                )
                gray = image_to_gray_array(img)
                if self._last_gray is None:
                    self._last_gray = gray
                    self.status.emit("監控中：已建立基準畫面")
                else:
                    diff = mean_abs_diff(self._last_gray, gray)
                    quiet = self._threshold * 0.45
                    now = time.monotonic()

                    if not self._wait_stable:
                        if diff >= self._threshold:
                            self._last_gray = gray
                            self._fire_if_cooldown()
                        else:
                            self._last_gray = gray * 0.2 + self._last_gray * 0.8
                            if now - self._idle_status_at > 8.0:
                                self._idle_status_at = now
                                self.status.emit("監控中（變化即辨識）…")
                    elif diff >= self._threshold:
                        # New significant change — restart stable timer
                        self._pending_change = True
                        self._stable_since = None
                        self._last_gray = gray
                        self.status.emit("偵測到變化，等待畫面穩定…")
                    elif self._pending_change:
                        if diff <= quiet:
                            if self._stable_since is None:
                                self._stable_since = now
                            held = now - self._stable_since
                            need = self._stable_s
                            pct = min(100, int(100 * held / need)) if need > 0 else 100
                            self.status.emit(
                                f"等待畫面穩定… {held * 1000:.0f}/{need * 1000:.0f} ms（{pct}%）"
                            )
                            self._last_gray = gray
                            if held >= need:
                                self._fire_if_cooldown()
                        else:
                            # Still jittering — reset stable clock
                            self._stable_since = None
                            self._last_gray = gray
                            self.status.emit("畫面仍在變化，繼續等待穩定…")
                    else:
                        self._last_gray = gray * 0.2 + self._last_gray * 0.8
                        if now - self._idle_status_at > 8.0:
                            self._idle_status_at = now
                            self.status.emit(
                                f"監控中（穩定 {int(self._stable_s * 1000)}ms 後辨識）…"
                            )
            except Exception as exc:
                self.error.emit(str(exc))
            time.sleep(self._interval_s)


class RegionMonitor(QObject):
    """Background region change detector."""

    region_changed = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: _MonitorWorker | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, cfg: AppConfig) -> None:
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
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
            self._worker = None
