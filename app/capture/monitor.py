"""Auto-monitor OCR region for visual changes (+ optional stable-duration wait)."""

from __future__ import annotations

import threading
import time
from copy import deepcopy

import numpy as np
from PySide6.QtCore import QObject, Signal

from app.capture.screenshot import capture_region, image_to_gray_array, mean_abs_diff
from app.config import AppConfig


class RegionMonitor(QObject):
    """Background region change detector (daemon thread + Qt signals)."""

    region_changed = Signal()
    error = Signal(str)
    status = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
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
        return t is not None and t.is_alive()

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

    def start(self, cfg: AppConfig) -> None:
        self.stop()
        self.configure(cfg)
        self.reset_baseline()
        self._stop.clear()
        t = threading.Thread(
            target=self._run_loop,
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

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while not self._stop.is_set() and time.monotonic() < end:
            time.sleep(min(0.05, end - time.monotonic()))

    def _fire_if_cooldown(self, cooldown_s: float, wait_stable: bool) -> None:
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
            self.status.emit("冷卻中，稍候再觸發…")
            return
        if wait_stable:
            self.status.emit("畫面已穩定，開始處理…")
        else:
            self.status.emit("偵測到變化，開始處理…")
        self.region_changed.emit()

    def _run_loop(self) -> None:
        with self._lock:
            mode = (
                f"穩定 {int(self._stable_s * 1000)}ms 後辨識"
                if self._wait_stable
                else "變化即辨識"
            )
        self.status.emit(f"監控中（{mode}）…")

        while not self._stop.is_set():
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
                self._sleep_interruptible(0.3)
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
                if self._stop.is_set():
                    break
                gray = image_to_gray_array(img)

                status_msg: str | None = None
                should_fire = False
                with self._lock:
                    if self._stop.is_set():
                        break
                    if self._last_gray is None:
                        self._last_gray = gray
                        status_msg = "監控中：已建立基準畫面"
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
                                    status_msg = "監控中（變化即辨識）…"
                        elif diff >= eff_threshold:
                            self._pending_change = True
                            self._stable_since = None
                            self._last_gray = gray
                            status_msg = "偵測到變化，等待畫面穩定…"
                        elif self._pending_change:
                            if diff <= quiet:
                                if self._stable_since is None:
                                    self._stable_since = now
                                held = now - self._stable_since
                                need = stable_s
                                pct = (
                                    min(100, int(100 * held / need)) if need > 0 else 100
                                )
                                status_msg = (
                                    f"等待畫面穩定… {held * 1000:.0f}/{need * 1000:.0f} ms"
                                    f"（{pct}%）"
                                )
                                self._last_gray = gray
                                if held >= need:
                                    should_fire = True
                            else:
                                self._stable_since = None
                                self._last_gray = gray
                                status_msg = "畫面仍在變化，繼續等待穩定…"
                        else:
                            self._last_gray = gray * 0.2 + self._last_gray * 0.8
                            if now - self._idle_status_at > 8.0:
                                self._idle_status_at = now
                                status_msg = (
                                    f"監控中（穩定 {int(stable_s * 1000)}ms 後辨識）…"
                                )

                if status_msg is not None:
                    self.status.emit(status_msg)
                if should_fire and not self._stop.is_set():
                    self._fire_if_cooldown(cooldown_s, wait_stable)
            except Exception as exc:
                if not self._stop.is_set():
                    self.error.emit(str(exc))
            self._sleep_interruptible(interval_s)
