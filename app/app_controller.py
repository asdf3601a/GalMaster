"""Wire UI, capture, pipeline, hotkeys, monitor, tray."""

from __future__ import annotations

from copy import deepcopy
import threading

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle

from app.capture.monitor import RegionMonitor
from app.capture.screenshot import capture_from_config
from app.capture.windows import (
    enum_windows,
    is_window_valid,
    screen_region_to_client,
)
from app.config import AppConfig, load_config, save_config
from app.hotkeys.global_hotkey import GlobalHotkeyFilter
from app.i18n import set_language, tr
from app.obs.server import ObsSubtitleServer
from app.pipeline import PipelineResult, TranslationPipeline
from app.pipeline_queue import buffer_cap
from app.session.capture_stage import CaptureStage
from app.translate.llm_translator import list_models
from app.ui.main_window import MainWindow
from app.ui.overlay_window import OverlayWindow
from app.ui.region_selector import RegionSelector


class AppController(QObject):
    """
    Orchestrates staged pipeline:
      Detect (RegionMonitor) → Capture → Process buffer → Present
    """

    _models_result = Signal(object, str)  # list[str] | None, error
    # Capture runs off the UI thread; result marshalled back here (img | None, err | None)
    _capture_finished = Signal(object, object)

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.cfg = load_config()
        set_language(getattr(self.cfg, "ui_language", "zh-Hant") or "zh-Hant")
        # Capture stage: Idle / Capturing + deferred force/auto (see docs/state-machine.md)
        self.capture_stage = CaptureStage()
        self._models_fetching = False

        self.main = MainWindow(self.cfg)
        self.overlay = OverlayWindow()
        self.selector = RegionSelector()
        self.pipeline = TranslationPipeline()
        self.monitor = RegionMonitor()
        self.hotkey = GlobalHotkeyFilter(self)
        self._capture_finished.connect(self._on_capture_finished)
        self.obs = ObsSubtitleServer()

        self._setup_tray()
        self._connect()
        self._apply_runtime_settings(self.cfg, register_hotkey=True)
        self.refresh_windows()

        if self.cfg.auto_monitor and self.cfg.has_region:
            self.monitor.start(self.cfg)
        self.main.set_monitor_running(self.monitor.is_running)
        if self.cfg.auto_monitor and self.cfg.has_region and not self.monitor.is_running:
            self.cfg.auto_monitor = False

        self.overlay.setGeometry(
            self.cfg.overlay_x,
            self.cfg.overlay_y,
            self.cfg.overlay_w,
            self.cfg.overlay_h,
        )
        self.overlay.retranslate()
        self.overlay.show()
        self.main.show()

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon = self.app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("GalMaster")
        menu = QMenu()
        self._tray_show_action = QAction(tr("tray.show_main"), self)
        self._tray_show_action.triggered.connect(self.main.show)
        self._tray_show_action.triggered.connect(self.main.raise_)
        self._tray_overlay_action = QAction(tr("tray.overlay_hide"), self)
        self._tray_overlay_action.triggered.connect(self.toggle_overlay)
        self._tray_translate_action = QAction(tr("tray.translate"), self)
        self._tray_translate_action.triggered.connect(lambda: self.translate_now(force=True))
        self._tray_quit_action = QAction(tr("tray.quit"), self)
        self._tray_quit_action.triggered.connect(self.shutdown)
        menu.addAction(self._tray_show_action)
        menu.addAction(self._tray_overlay_action)
        menu.addAction(self._tray_translate_action)
        menu.addSeparator()
        menu.addAction(self._tray_quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _retranslate_tray(self) -> None:
        if hasattr(self, "_tray_show_action"):
            self._tray_show_action.setText(tr("tray.show_main"))
        if hasattr(self, "_tray_translate_action"):
            self._tray_translate_action.setText(tr("tray.translate"))
        if hasattr(self, "_tray_quit_action"):
            self._tray_quit_action.setText(tr("tray.quit"))
        self._on_overlay_visibility(self.overlay.isVisible())

    def _connect(self) -> None:
        self.main.select_region_clicked.connect(self.start_region_select)
        self.main.refresh_windows_clicked.connect(self.refresh_windows)
        self.main.bind_window_changed.connect(self.on_bind_window)
        self.main.translate_now_clicked.connect(lambda: self.translate_now(force=True))
        self.main.auto_monitor_toggled.connect(self.on_auto_monitor)
        self.main.apply_settings_clicked.connect(self.on_apply_settings)
        self.main.save_settings_clicked.connect(self.on_save_settings)
        self.main.cancel_settings_clicked.connect(self.on_cancel_settings)
        self.main.toggle_overlay_clicked.connect(self.toggle_overlay)
        self.main.clear_cache_clicked.connect(self.pipeline.clear_cache)
        self.main.exit_requested.connect(self.shutdown)
        self.main.copy_obs_url_clicked.connect(self._copy_obs_url)
        self.main.refresh_models_clicked.connect(self.refresh_models)
        self._models_result.connect(self._on_models_result)

        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.cancelled.connect(self.on_region_cancelled)

        self.pipeline.progress.connect(self.on_progress)
        self.pipeline.finished.connect(self.on_pipeline_finished)
        self.pipeline.busy_changed.connect(self._on_pipeline_busy)
        self.pipeline.preview_ready.connect(self.on_preview_ready)

        # Auto-monitor: do not force (skip unchanged text; status only)
        self.monitor.region_changed.connect(lambda: self.translate_now(force=False))
        self.monitor.error.connect(
            lambda e: self.main.set_status(tr("monitor.error", err=e))
        )
        self.monitor.status.connect(self.on_monitor_status)

        # Manual hotkey always runs full pipeline
        self.hotkey.activated.connect(lambda: self.translate_now(force=True))
        self.app.installNativeEventFilter(self.hotkey)

        self.overlay.visibility_changed.connect(self._on_overlay_visibility)
        self.overlay.geometry_changed.connect(self._on_overlay_geometry_changed)
        # Close main window (X) = full quit so Overlay / monitor / tray all go away
        self.main.hide_to_tray_requested.connect(self.shutdown)
        self.overlay.closed.connect(self._on_overlay_closed)

        self._on_overlay_visibility(self.overlay.isVisible())

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()

    def _on_overlay_visibility(self, visible: bool) -> None:
        self.main.set_overlay_button_state(visible)
        if hasattr(self, "_tray_overlay_action") and self._tray_overlay_action is not None:
            self._tray_overlay_action.setText(
                tr("tray.overlay_hide") if visible else tr("tray.overlay_show")
            )

    def _on_overlay_geometry_changed(self) -> None:
        """Persist overlay position/size after user move or edge-resize."""
        try:
            g = self.overlay.geometry()
            self.cfg.overlay_x, self.cfg.overlay_y = g.x(), g.y()
            self.cfg.overlay_w, self.cfg.overlay_h = g.width(), g.height()
            self.persist()
        except Exception:
            pass

    def _on_overlay_closed(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        # Overlay closed via its own UI — only update button state (do not quit)
        self.main.set_overlay_button_state(False)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.main.show()
            self.main.raise_()

    def refresh_windows(self) -> None:
        wins = enum_windows()
        self.main.set_windows(wins)
        self.main.set_status(tr("status.windows_list", n=len(wins)))

    def on_bind_window(self, hwnd: int, title: str) -> None:
        self.cfg.bound_hwnd = hwnd if is_window_valid(hwnd) or hwnd == 0 else 0
        self.cfg.bound_title = title if hwnd else ""
        self.main.set_region_info(self.cfg)
        self.persist()
        if self.monitor.is_running:
            self.monitor.configure(self.cfg)

    def start_region_select(self) -> None:
        self.main.set_status(tr("status.selecting_region"))
        # Remember visibility so cancel can restore (not leave UI gone)
        self._pre_select_main_visible = self.main.isVisible()
        self._pre_select_overlay_visible = self.overlay.isVisible()
        self.overlay.hide()
        self.main.hide()
        self.selector.start()

    def _restore_after_select(self) -> None:
        """Bring main/overlay back after region select finishes or is cancelled."""
        if getattr(self, "_pre_select_main_visible", True):
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()
        else:
            self.main.show()
            self.main.raise_()
        if getattr(self, "_pre_select_overlay_visible", True):
            self.overlay.show()
        # Always ensure main is usable after select (user started select from main)
        if not self.main.isVisible():
            self.main.show()

    def on_region_cancelled(self) -> None:
        self._restore_after_select()
        self.main.set_status(tr("status.region_cancelled"), busy=False)

    def on_region_selected(self, x: int, y: int, w: int, h: int) -> None:
        # x,y,w,h are already physical pixels (see RegionSelector + dpi mapping)
        self._restore_after_select()
        # Always remember absolute screen rect for stale-HWND degraded capture
        self.cfg.set_abs_region(x, y, w, h)
        hwnd = self.cfg.bound_hwnd
        if hwnd and is_window_valid(hwnd):
            rx, ry, rw, rh = screen_region_to_client(hwnd, x, y, w, h)
            self.cfg.set_region(rx, ry, rw, rh)
        else:
            self.cfg.bound_hwnd = 0
            self.cfg.bound_title = ""
            self.cfg.set_region(x, y, w, h)
        self.main.set_region_info(self.cfg)
        self.pipeline.reset_dedupe()
        self.pipeline.clear_history()
        self.monitor.reset_baseline()
        self.persist()
        self.main.set_status(tr("status.region_set", w=w, h=h))
        if self.cfg.auto_monitor:
            self.monitor.start(self.cfg)
            running = self.monitor.is_running
            self.main.set_monitor_running(running)
            if not running:
                self.cfg.auto_monitor = False

    def on_apply_settings(self) -> None:
        self._apply_from_ui(persist=False)
        self.main.set_status(tr("status.settings_applied"), busy=False)

    def on_save_settings(self) -> None:
        self._apply_from_ui(persist=True)
        self.main.set_status(tr("status.settings_saved"), busy=False)

    def on_cancel_settings(self) -> None:
        self.main.set_config(self.cfg)
        # Restore auto-monitor UI state vs runtime
        if self.cfg.auto_monitor and self.cfg.has_region and not self.monitor.is_running:
            self.monitor.start(self.cfg)
        elif not self.cfg.auto_monitor and self.monitor.is_running:
            self.monitor.stop()
        self.main.set_monitor_running(self.monitor.is_running)
        self.main.set_status(tr("status.settings_restored"), busy=False)

    def _apply_from_ui(self, *, persist: bool) -> None:
        old = deepcopy(self.cfg)
        self.cfg = self.main.collect_config()
        # Preserve abs region / hwnd geometry fields not edited in form
        self.cfg.region_abs_x = old.region_abs_x
        self.cfg.region_abs_y = old.region_abs_y
        self.cfg.region_abs_w = old.region_abs_w
        self.cfg.region_abs_h = old.region_abs_h
        self.cfg.region_x = old.region_x
        self.cfg.region_y = old.region_y
        self.cfg.region_w = old.region_w
        self.cfg.region_h = old.region_h
        self.cfg.bound_hwnd = old.bound_hwnd
        self.cfg.bound_title = old.bound_title
        self._apply_runtime_settings(
            self.cfg,
            register_hotkey=(self.cfg.hotkey != old.hotkey),
            old_auto=old.auto_monitor,
        )
        self.main.mark_applied(self.cfg)
        if persist:
            self.persist()

    def _apply_runtime_settings(
        self,
        cfg: AppConfig,
        *,
        register_hotkey: bool = False,
        old_auto: bool | None = None,
    ) -> None:
        set_language(getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant")
        self.overlay.set_opacity_level(cfg.overlay_opacity)
        self.overlay.apply_style(cfg)
        self.overlay.set_click_through(cfg.overlay_click_through)
        self.overlay.retranslate()
        self._retranslate_tray()
        if register_hotkey:
            self._register_hotkey()
        if self.monitor.is_running:
            self.monitor.configure(cfg)
        # Sync auto-monitor runtime if flag changed via Apply/Save
        if old_auto is not None and cfg.auto_monitor != old_auto:
            if cfg.auto_monitor:
                if cfg.has_region:
                    self.pipeline.reset_dedupe()
                    self.monitor.start(cfg)
            else:
                self.monitor.stop()
        elif cfg.auto_monitor and cfg.has_region and not self.monitor.is_running:
            self.monitor.start(cfg)
        elif not cfg.auto_monitor and self.monitor.is_running:
            self.monitor.stop()
        self.main.set_monitor_running(self.monitor.is_running)
        self._sync_obs_server(cfg)

    def on_auto_monitor(self, enabled: bool) -> None:
        """Immediate operational toggle (like bind-window): apply monitor fields + persist, no dirty."""
        if enabled:
            if not self.cfg.has_region:
                self.main.show_error(
                    tr("monitor.need_region_title"), tr("monitor.need_region")
                )
                self.cfg.auto_monitor = False
                self.main.set_monitor_running(False)
                return
            # Pull monitor timing from form so Start uses current spin values
            draft = self.main.collect_config()
            self.cfg.monitor_stable_ms = draft.monitor_stable_ms
            self.cfg.monitor_wait_stable = draft.monitor_stable_ms > 0
            self.cfg.monitor_interval_ms = draft.monitor_interval_ms
            self.cfg.monitor_cooldown_ms = draft.monitor_cooldown_ms
            self.cfg.monitor_diff_threshold = draft.monitor_diff_threshold
            self.cfg.pipeline_buffer_size = buffer_cap(
                getattr(draft, "pipeline_buffer_size", 3)
            )
            self.cfg.window_capture_method = draft.window_capture_method
            self.pipeline.reset_dedupe()
            self.pipeline.clear_auto_queue()
            self.capture_stage.reset_deferred()
            self.monitor.start(self.cfg)
            running = self.monitor.is_running
            self.cfg.auto_monitor = running
            self.main.set_monitor_running(running)
            # Sync applied snapshot monitor fields (operational, not draft dirty)
            self.main.sync_operational_monitor(self.cfg)
            try:
                self.persist()
            except Exception:
                pass
            if not running:
                self.main.set_status(tr("status.monitor_start_failed"), busy=False)
                return
            if self.cfg.monitor_stable_ms > 0:
                mode = tr("monitor.mode_stable", ms=self.cfg.monitor_stable_ms)
            else:
                mode = tr("monitor.mode_immediate")
            self.main.set_status(tr("status.monitor_on", mode=mode), busy=False)
        else:
            # Stop must not wait on OCR/LLM: signal monitor off, drop auto backlog.
            self.cfg.auto_monitor = False
            self.monitor.stop(wait=0.15)
            self.capture_stage.reset_deferred()
            try:
                self.pipeline.clear_auto_queue()
            except Exception:
                pass
            self.main.set_monitor_running(False)
            self.main.sync_operational_monitor(self.cfg)
            try:
                self.persist()
            except Exception:
                pass
            self.main.set_status(tr("status.monitor_off"), busy=False)

    def _register_hotkey(self) -> None:
        try:
            hwnd = int(self.main.winId())
            self.hotkey.register(hwnd, self.cfg.hotkey)
            self.main.set_status(
                tr("status.hotkey_registered", hotkey=self.cfg.hotkey)
            )
        except Exception as exc:
            self.main.set_status(tr("status.hotkey_failed", err=exc))

    def _ensure_capture_target(self) -> bool:
        """
        Validate HWND / region before capture.
        On stale HWND with abs cache: clear hwnd and use absolute screen coords.
        """
        if not self.cfg.has_region and not self.cfg.has_abs_region:
            self.main.set_status(tr("status.need_region"), busy=False)
            try:
                self.overlay.set_status(tr("status.error"))
            except Exception:
                pass
            return False

        if self.cfg.bound_hwnd and not is_window_valid(self.cfg.bound_hwnd):
            if self.cfg.has_abs_region:
                self.main.set_status(tr("status.hwnd_stale_abs"), busy=True)
                self.cfg.bound_hwnd = 0
                self.cfg.bound_title = ""
                # Client-relative region is no longer meaningful — use abs as region
                self.cfg.set_region(
                    self.cfg.region_abs_x,
                    self.cfg.region_abs_y,
                    self.cfg.region_abs_w,
                    self.cfg.region_abs_h,
                )
                self.main.set_region_info(self.cfg)
                try:
                    self.persist()
                except Exception:
                    pass
            else:
                self.main.set_status(tr("status.hwnd_stale"), busy=False)
                try:
                    self.overlay.set_status(tr("status.error"))
                except Exception:
                    pass
                return False
        return True

    def translate_now(self, *, force: bool = True) -> None:
        """
        Capture stage entry (Detect fire / manual / hotkey).

        force=True (manual button / hotkey): always process even if text unchanged;
        clears waiting auto jobs when enqueued.
        force=False (auto-monitor): may queue while Process is busy (bounded buffer).
        """
        # Drop late auto fires after the user stopped monitoring (stop is non-blocking).
        if not force and not self.cfg.auto_monitor:
            return

        if not self._ensure_capture_target():
            return

        cap = buffer_cap(getattr(self.cfg, "pipeline_buffer_size", 3))
        action = self.capture_stage.request(force=force, buffer_cap=cap)
        if action == "deferred":
            return

        if force:
            # Manual path: drop waiting auto Process backlog
            self.pipeline.clear_auto_queue()

        self._start_capture(force=force)

    def _start_capture(self, *, force: bool) -> None:
        """Begin grab (phase already CAPTURING from request/pump, or set here)."""
        self.capture_stage.begin_grab(
            force=force,
            overlay_was_visible=self.overlay.isVisible(),
            overlay_opacity=self.overlay.windowOpacity(),
        )
        self.main.set_status(tr("pipe.capturing"), busy=True)
        # Cloak only when capture may use *screen* pixels (unbound region / no HWND).
        # Bound-window WGC/GDI does not include our overlay top-level window.
        need_cloak = (
            self.capture_stage.overlay_was_visible
            and self._capture_may_use_screen()
            and self._overlay_may_cover_region()
        )
        if need_cloak:
            self.overlay.setWindowOpacity(0.0)
            self.capture_stage.mark_cloaked()
            QTimer.singleShot(40, self._capture_and_enqueue)
        else:
            QTimer.singleShot(0, self._capture_and_enqueue)

    def _restore_overlay_after_capture(self) -> None:
        """Undo temporary opacity cloak used during capture."""
        saved = self.capture_stage.take_cloak_restore()
        if saved is not None:
            self.overlay.setWindowOpacity(saved)
        if self.capture_stage.overlay_was_visible and not self.overlay.isVisible():
            self.overlay.show()

    def _capture_may_use_screen(self) -> bool:
        """True when capture path is likely display/mss (overlay can appear in shot)."""
        hwnd = int(getattr(self.cfg, "bound_hwnd", 0) or 0)
        if hwnd and is_window_valid(hwnd):
            # Window capture primary — overlay is a different top-level HWND
            return False
        return True

    def _overlay_may_cover_region(self) -> bool:
        """True when overlay geometry likely intersects the OCR capture rect."""
        if not self.overlay.isVisible():
            return False
        try:
            from app.capture.dpi import qt_rect_to_physical
            from app.capture.windows import client_to_screen_rect, is_window_valid

            # Capture rect in physical screen pixels
            cfg = self.cfg
            if cfg.bound_hwnd and is_window_valid(cfg.bound_hwnd):
                rx, ry, rw, rh = client_to_screen_rect(
                    cfg.bound_hwnd,
                    cfg.region_x,
                    cfg.region_y,
                    cfg.region_w,
                    cfg.region_h,
                )
            elif cfg.has_abs_region:
                rx, ry, rw, rh = (
                    int(cfg.region_abs_x),
                    int(cfg.region_abs_y),
                    int(cfg.region_abs_w),
                    int(cfg.region_abs_h),
                )
            elif cfg.has_region:
                rx, ry, rw, rh = (
                    int(cfg.region_x),
                    int(cfg.region_y),
                    int(cfg.region_w),
                    int(cfg.region_h),
                )
            else:
                return True  # unknown region: be safe, cloak

            # Overlay in physical pixels (Qt logical → physical)
            g = self.overlay.frameGeometry()
            ox, oy, ow, oh = qt_rect_to_physical(g.x(), g.y(), g.width(), g.height())

            # Expand slightly so partial edge overlap still cloaks
            pad = 4
            return not (
                ox + ow + pad <= rx
                or rx + rw + pad <= ox
                or oy + oh + pad <= ry
                or ry + rh + pad <= oy
            )
        except Exception:
            return True

    def _capture_and_enqueue(self) -> None:
        """Kick off capture on a worker thread (WGC must not block the Qt UI)."""
        cfg_snap = deepcopy(self.cfg)

        def _work() -> None:
            try:
                img = capture_from_config(cfg_snap)
                self._capture_finished.emit(img, None)
            except Exception as exc:
                self._capture_finished.emit(None, exc)

        threading.Thread(target=_work, name="galmaster-capture", daemon=True).start()

    def _on_capture_finished(self, img: object, err: object) -> None:
        force, was_visible = self.capture_stage.finish()
        self._restore_overlay_after_capture()
        if err is not None:
            # Status only — keep last overlay / result text (same as empty-OCR soft path)
            self.main.set_status(tr("status.capture_failed", err=err), busy=False)
            try:
                self.overlay.set_status(tr("status.error"))
            except Exception:
                pass
            # Still pump deferred captures after a failed grab
            self._capture_pump_deferred()
            return
        if was_visible:
            self.overlay.set_status(tr("status.processing"))
        # Process stage: bounded FIFO absorbs backlog while worker is busy
        self.pipeline.request(self.cfg, img, force=force)
        depth = self.pipeline.queue_depth
        if depth > 0:
            cap = buffer_cap(getattr(self.cfg, "pipeline_buffer_size", 3))
            self.main.set_status(
                tr("pipe.queued", n=depth, max=cap), busy=True
            )
        self._capture_pump_deferred()

    def _capture_pump_deferred(self) -> None:
        """After a capture ends, start force recapture or deferred auto grabs."""
        nxt = self.capture_stage.pump()
        if nxt == "force":
            self.pipeline.clear_auto_queue()
            self._start_capture(force=True)
        elif nxt == "auto":
            self._start_capture(force=False)

    def on_preview_ready(self, img: object) -> None:
        try:
            self.main.set_preview_image(img)  # type: ignore[arg-type]
        except Exception:
            pass

    def _sync_obs_server(self, cfg: AppConfig) -> None:
        def _i(name: str, default: int) -> int:
            val = getattr(cfg, name, default)
            if val is None or val == "":
                return default
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        self.obs.configure(
            show_source=bool(getattr(cfg, "obs_show_source", False)),
            show_translation=bool(getattr(cfg, "obs_show_translation", True)),
            font_family=str(getattr(cfg, "obs_font_family", "") or ""),
            source_font_size=max(10, _i("obs_source_font_size", 20)),
            translation_font_size=max(
                10, _i("obs_translation_font_size", _i("obs_font_size", 28))
            ),
            source_color=str(getattr(cfg, "obs_source_color", "#d8d8e0") or "#d8d8e0"),
            translation_color=str(
                getattr(cfg, "obs_translation_color", "#ffffff") or "#ffffff"
            ),
            translation_bold=bool(getattr(cfg, "obs_translation_bold", True)),
            text_align=str(getattr(cfg, "obs_text_align", "left") or "left"),
            bg_color=str(getattr(cfg, "obs_bg_color", "#000000") or "#000000"),
            bg_alpha=_i("obs_bg_alpha", 140),
        )
        if getattr(cfg, "obs_enabled", False):
            port = int(getattr(cfg, "obs_port", 8765) or 8765)
            self.obs.start(port)
            if self.obs.running:
                self.main.set_obs_status(running=True, url=self.obs.url())
            else:
                self.main.set_obs_status(
                    running=False, error=self.obs.last_error or "bind failed"
                )
        else:
            self.obs.stop()
            self.main.set_obs_status(running=False)

    def _copy_obs_url(self) -> None:
        url = self.main.obs_url()
        QGuiApplication.clipboard().setText(url)
        self.main.set_status(tr("obs.copied"), busy=False)

    def refresh_models(self, role: str = "translate") -> None:
        """Fetch GET /models using current form API settings (background thread)."""
        if self._models_fetching:
            return
        role = (role or "translate").strip().lower()
        if role not in ("translate", "vlm"):
            role = "translate"
        draft = self.main.collect_config()
        if role == "vlm":
            api_key = (getattr(draft, "vlm_api_key", "") or "").strip()
            base_url = (getattr(draft, "vlm_base_url", "") or "").strip()
            protocol = getattr(draft, "vlm_api_protocol", "openai") or "openai"
            anthropic_version = (
                getattr(draft, "vlm_anthropic_version", "2023-06-01") or "2023-06-01"
            )
        else:
            api_key = (draft.api_key or "").strip()
            base_url = (draft.base_url or "").strip()
            protocol = draft.api_protocol or "openai"
            anthropic_version = draft.anthropic_version or "2023-06-01"

        if not api_key:
            self.main.set_models_status(tr("models.need_key"), role=role)
            self.main.set_status(tr("models.need_key"), busy=False)
            return
        if not base_url:
            self.main.set_models_status(tr("models.need_url"), role=role)
            self.main.set_status(tr("models.need_url"), busy=False)
            return

        self._models_fetching = True
        self._models_fetch_role = role
        self.main.set_models_refreshing(True, role=role)

        def work() -> None:
            try:
                ids = list_models(
                    api_key=api_key,
                    base_url=base_url,
                    protocol=protocol,
                    anthropic_version=anthropic_version,
                )
                self._models_result.emit(ids, "")
            except Exception as exc:
                self._models_result.emit(None, str(exc))

        threading.Thread(target=work, name="list-models", daemon=True).start()

    def _on_models_result(self, models: object, error: str) -> None:
        role = getattr(self, "_models_fetch_role", "translate") or "translate"
        self._models_fetching = False
        self.main.set_models_refreshing(False, role=role)
        if error or models is None:
            msg = tr("models.failed", err=error or "unknown")
            self.main.set_models_status(msg, role=role)
            self.main.set_status(msg, busy=False)
            return
        assert isinstance(models, list)
        self.main.set_models_list(models, keep_current=True, role=role)
        msg = tr("models.ok", n=len(models))
        self.main.set_models_status(msg, role=role)
        self.main.set_status(msg, busy=False)

    def on_monitor_status(self, msg: str) -> None:
        if self.pipeline.busy:
            return
        # Treat "waiting for stable / about to fire" as busy for the status pill
        busy = msg in (
            tr("monitor.waiting_stable"),
            tr("monitor.still_changing"),
            tr("monitor.stable_fire"),
            tr("monitor.change_fire"),
            tr("monitor.cooldown"),
        )
        if not busy:
            # progress template: prefix before first format field
            sample = tr("monitor.stable_progress", held="\0", need="0", pct=0)
            prefix = sample.split("\0", 1)[0]
            if prefix and msg.startswith(prefix):
                busy = True
        self.main.set_status(msg, busy=busy)

    def _on_pipeline_busy(self, busy: bool) -> None:
        self.main.set_busy(busy)
        if busy:
            self.main.set_status(tr("status.processing"), busy=True)

    def on_progress(self, msg: str) -> None:
        self.main.set_status(msg, busy=True)
        if self.overlay.isVisible():
            self.overlay.set_status(msg)

    def on_pipeline_finished(self, result: object) -> None:
        assert isinstance(result, PipelineResult)
        try:
            self._present(result)
        except Exception as exc:
            # Never leave the UI stuck busy / unresponsive after a result.
            try:
                self.main.set_status(tr("pipe.error", err=str(exc)), busy=False)
            except Exception:
                pass

    def _present(self, result: PipelineResult) -> None:
        """Present stage: apply Process result to main UI / Overlay / OBS."""
        # Unchanged / no text / blank capture: status only — keep overlay + result panel
        if result.skipped:
            msg = result.status_message or tr("pipe.unchanged")
            self.main.set_status(msg, busy=False)
            if self.overlay.isVisible():
                self.overlay.set_status(msg)
            return

        # Show results on overlay when it is open, or was open when capture started
        if self.capture_stage.overlay_was_visible or self.overlay.isVisible():
            if not self.overlay.isVisible():
                self.overlay.show()

        # Hard error with no source: status + do NOT wipe last good overlay/result
        # (user can still read the previous translation while diagnosing the fault)
        if result.error and not result.source_text:
            self.main.set_status(result.error, busy=False)
            if self.overlay.isVisible():
                self.overlay.set_status(result.error)
            return
        if result.error and result.source_text and not result.translated_text:
            self.main.set_status(result.error, busy=False)
            self.main.append_result_line(
                self._format_result_line(
                    source=result.source_text,
                    translation="",
                    err=result.error,
                )
            )
            self.overlay.set_content(
                source=result.source_text,
                translation=result.error,
                status=result.error,
                show=False,
            )
            # Keep stream in sync with soft failure (source + error as translation)
            try:
                self.obs.publish(
                    source=result.source_text,
                    translation=result.error,
                    status="error",
                )
            except Exception:
                pass
            return

        if result.ocr_only:
            self.main.append_result_line(
                self._format_result_line(
                    source=result.source_text,
                    translation="",
                    ocr_only=True,
                )
            )
            self.main.set_status(tr("pipe.ocr_done"), busy=False)
            self.overlay.set_content(
                source=result.source_text,
                translation=result.source_text,
                status=tr("pipe.ocr_only_status"),
                show=False,
            )
            try:
                self.obs.publish(
                    source=result.source_text,
                    translation=result.source_text,
                    status="ocr_only",
                )
            except Exception:
                pass
            return

        cache_tag = tr("pipe.cache_tag") if result.from_cache else ""
        self.main.append_result_line(
            self._format_result_line(
                source=result.source_text,
                translation=result.translated_text,
                from_cache=result.from_cache,
            )
        )
        self.main.set_status(tr("pipe.done") + cache_tag, busy=False)
        self.overlay.set_content(
            source=result.source_text,
            translation=result.translated_text,
            status=tr("pipe.done") + cache_tag,
            show=False,
        )
        try:
            self.obs.publish(
                source=result.source_text,
                translation=result.translated_text,
                status="ok" + ("_cache" if result.from_cache else ""),
            )
        except Exception:
            pass

    @staticmethod
    def _format_result_line(
        *,
        source: str,
        translation: str = "",
        err: str = "",
        ocr_only: bool = False,
        from_cache: bool = False,
    ) -> str:
        """One-line concise log entry for the main-window result history."""

        def _flat(s: str) -> str:
            return " ".join((s or "").split())

        src = _flat(source)
        if err:
            return tr("result.line_error", source=src, err=_flat(err))
        if ocr_only or not _flat(translation):
            return tr("result.line_ocr", source=src)
        line = tr(
            "result.line_full",
            source=src,
            translation=_flat(translation),
        )
        if from_cache:
            line = f"{line} {tr('pipe.cache_tag')}".rstrip()
        return line

    def persist(self) -> None:
        g = self.overlay.geometry()
        self.cfg.overlay_x, self.cfg.overlay_y = g.x(), g.y()
        self.cfg.overlay_w, self.cfg.overlay_h = g.width(), g.height()
        mg = self.main.geometry()
        self.cfg.main_window_x, self.cfg.main_window_y = mg.x(), mg.y()
        self.cfg.main_window_w, self.cfg.main_window_h = mg.width(), mg.height()
        save_config(self.cfg)

    def shutdown(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        try:
            # Persist applied cfg + geometry (not unapplied dirty form fields)
            self.persist()
        except Exception:
            pass
        try:
            self.monitor.stop()
        except Exception:
            pass
        try:
            self.hotkey.unregister()
            self.app.removeNativeEventFilter(self.hotkey)
        except Exception:
            pass
        try:
            self.pipeline.shutdown()
        except Exception:
            pass
        try:
            self.obs.stop()
        except Exception:
            pass
        self.tray.hide()
        self.overlay.hide()
        self.main.hide()
        self.overlay.close()
        self.main.force_close()
        self.app.quit()
