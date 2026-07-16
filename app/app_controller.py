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
from app.translate.llm_translator import list_models
from app.ui.main_window import MainWindow
from app.ui.overlay_window import OverlayWindow
from app.ui.region_selector import RegionSelector


class AppController(QObject):
    _models_result = Signal(object, str)  # list[str] | None, error

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.cfg = load_config()
        set_language(getattr(self.cfg, "ui_language", "zh-Hant") or "zh-Hant")
        self._capturing = False
        self._overlay_was_visible = True
        self._pending_force = True
        self._models_fetching = False

        self.main = MainWindow(self.cfg)
        self.overlay = OverlayWindow()
        self.selector = RegionSelector()
        self.pipeline = TranslationPipeline()
        self.monitor = RegionMonitor()
        self.hotkey = GlobalHotkeyFilter(self)
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
        self.monitor.error.connect(lambda e: self.main.set_status(f"監控錯誤：{e}"))
        self.monitor.status.connect(self.on_monitor_status)

        # Manual hotkey always runs full pipeline
        self.hotkey.activated.connect(lambda: self.translate_now(force=True))
        self.app.installNativeEventFilter(self.hotkey)

        self.overlay.visibility_changed.connect(self._on_overlay_visibility)
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
        self.main.set_status(f"視窗列表：{len(wins)} 個")

    def on_bind_window(self, hwnd: int, title: str) -> None:
        self.cfg.bound_hwnd = hwnd if is_window_valid(hwnd) or hwnd == 0 else 0
        self.cfg.bound_title = title if hwnd else ""
        self.main.set_region_info(self.cfg)
        self.persist()
        if self.monitor.is_running:
            self.monitor.update_config(self.cfg)

    def start_region_select(self) -> None:
        self.main.set_status("框選 OCR 區域中…")
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
        self.main.set_status("已取消框選", busy=False)

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
        self.main.set_status(f"已設定區域 {w}×{h}（實體像素）")
        if self.cfg.auto_monitor:
            self.monitor.start(self.cfg)
            running = self.monitor.is_running
            self.main.set_monitor_running(running)
            if not running:
                self.cfg.auto_monitor = False

    def on_apply_settings(self) -> None:
        self._apply_from_ui(persist=False)
        self.main.set_status("已套用設定", busy=False)

    def on_save_settings(self) -> None:
        self._apply_from_ui(persist=True)
        self.main.set_status("已儲存設定", busy=False)

    def on_cancel_settings(self) -> None:
        self.main.set_config(self.cfg)
        # Restore auto-monitor UI state vs runtime
        if self.cfg.auto_monitor and self.cfg.has_region and not self.monitor.is_running:
            self.monitor.start(self.cfg)
        elif not self.cfg.auto_monitor and self.monitor.is_running:
            self.monitor.stop()
        self.main.set_monitor_running(self.monitor.is_running)
        self.main.set_status("已還原設定", busy=False)

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
            self.monitor.update_config(cfg)
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
        # Immediate operational toggle via top-bar Start/Stop; mark dirty so Save persists
        if enabled:
            if not self.cfg.has_region:
                self.main.show_error("自動監控", "請先框選 OCR 區域")
                self.cfg.auto_monitor = False
                self.main.set_monitor_running(False)
                return
            # Pull wait-stable / thresholds from form draft so start feels responsive
            draft = self.main.collect_config()
            self.cfg.monitor_stable_ms = draft.monitor_stable_ms
            self.cfg.monitor_wait_stable = draft.monitor_stable_ms > 0
            self.cfg.monitor_interval_ms = draft.monitor_interval_ms
            self.cfg.monitor_cooldown_ms = draft.monitor_cooldown_ms
            self.cfg.monitor_diff_threshold = draft.monitor_diff_threshold
            self.pipeline.reset_dedupe()
            self.monitor.start(self.cfg)
            running = self.monitor.is_running
            self.cfg.auto_monitor = running
            self.main.set_monitor_running(running)
            self.main.mark_runtime_dirty()
            if not running:
                self.main.set_status(
                    "自動監控啟動失敗（先前監控執行緒可能仍在結束）", busy=False
                )
                return
            if self.cfg.monitor_stable_ms > 0:
                mode = f"穩定 {self.cfg.monitor_stable_ms}ms 後辨識"
            else:
                mode = "變化即辨識"
            self.main.set_status(f"自動監控已開啟（{mode}）…", busy=False)
        else:
            self.cfg.auto_monitor = False
            self.monitor.stop()
            self.main.set_monitor_running(False)
            self.main.mark_runtime_dirty()
            self.main.set_status("自動監控已關閉", busy=False)

    def _register_hotkey(self) -> None:
        try:
            hwnd = int(self.main.winId())
            self.hotkey.register(hwnd, self.cfg.hotkey)
            self.main.set_status(f"熱鍵已註冊: {self.cfg.hotkey}")
        except Exception as exc:
            self.main.set_status(f"熱鍵註冊失敗: {exc}")

    def _ensure_capture_target(self) -> bool:
        """
        Validate HWND / region before capture.
        On stale HWND with abs cache: clear hwnd and use absolute screen coords.
        """
        if not self.cfg.has_region and not self.cfg.has_abs_region:
            self.main.set_status("請先框選 OCR 區域", busy=False)
            self.overlay.set_content(translation="請先框選 OCR 區域", status="錯誤")
            return False

        if self.cfg.bound_hwnd and not is_window_valid(self.cfg.bound_hwnd):
            if self.cfg.has_abs_region:
                self.main.set_status(
                    "綁定視窗已失效，改用上次螢幕座標擷取…", busy=True
                )
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
                self.main.set_status(
                    "綁定視窗已失效，請重新框選 OCR 區域", busy=False
                )
                self.overlay.set_content(
                    translation="綁定視窗已失效，請重新框選 OCR 區域",
                    status="錯誤",
                )
                return False
        return True

    def translate_now(self, *, force: bool = True) -> None:
        """
        Run capture → OCR → translate.

        force=True (manual button / hotkey): always run even if text/image unchanged.
        force=False (auto-monitor): pipeline may skip when OCR text is identical.
        """
        if self._capturing:
            return
        # Auto-monitor: skip while pipeline is busy to avoid thrashing overlay hide/show.
        # Manual/hotkey (force=True) still runs (pipeline may coalesce pending jobs).
        if not force and self.pipeline.busy:
            return
        if not self._ensure_capture_target():
            return

        self._capturing = True
        self._pending_force = force
        self._overlay_was_visible = self.overlay.isVisible()
        self.main.set_status("截圖中…", busy=True)
        # Hide overlay so it never covers the OCR region; defer capture one tick
        # (no processEvents re-entrancy)
        self.overlay.hide()
        QTimer.singleShot(40, self._capture_and_enqueue)

    def _capture_and_enqueue(self) -> None:
        force = self._pending_force
        was_visible = self._overlay_was_visible
        try:
            img = capture_from_config(self.cfg)
        except Exception as exc:
            self._capturing = False
            if was_visible:
                self.overlay.show()
            self.main.set_status(f"截圖失敗：{exc}", busy=False)
            self.overlay.set_content(translation=str(exc), status="錯誤")
            return

        self._capturing = False
        # Restore previous overlay visibility; always show status when it was open
        if was_visible:
            self.overlay.show()
            self.overlay.set_status("處理中…")

        self.pipeline.request(self.cfg, img, force=force)

    def on_preview_ready(self, img: object) -> None:
        try:
            self.main.set_preview_image(img)  # type: ignore[arg-type]
        except Exception:
            pass

    def _sync_obs_server(self, cfg: AppConfig) -> None:
        self.obs.configure(
            show_source=bool(getattr(cfg, "obs_show_source", False)),
            show_translation=bool(getattr(cfg, "obs_show_translation", True)),
            font_family=str(getattr(cfg, "obs_font_family", "") or ""),
            source_font_size=int(getattr(cfg, "obs_source_font_size", 20) or 20),
            translation_font_size=int(
                getattr(
                    cfg,
                    "obs_translation_font_size",
                    getattr(cfg, "obs_font_size", 28),
                )
                or 28
            ),
            source_color=str(getattr(cfg, "obs_source_color", "#d8d8e0") or "#d8d8e0"),
            translation_color=str(
                getattr(cfg, "obs_translation_color", "#ffffff") or "#ffffff"
            ),
            translation_bold=bool(getattr(cfg, "obs_translation_bold", True)),
            text_align=str(getattr(cfg, "obs_text_align", "left") or "left"),
            bg_color=str(getattr(cfg, "obs_bg_color", "#000000") or "#000000"),
            bg_alpha=int(getattr(cfg, "obs_bg_alpha", 140) or 140),
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

    def refresh_models(self) -> None:
        """Fetch GET /models using current form API settings (background thread)."""
        if self._models_fetching:
            return
        draft = self.main.collect_config()
        if not (draft.api_key or "").strip():
            self.main.set_models_status(tr("models.need_key"))
            self.main.set_status(tr("models.need_key"), busy=False)
            return
        if not (draft.base_url or "").strip():
            self.main.set_models_status(tr("models.need_url"))
            return

        self._models_fetching = True
        self.main.set_models_refreshing(True)
        api_key = draft.api_key
        base_url = draft.base_url
        protocol = draft.api_protocol or "openai"
        anthropic_version = draft.anthropic_version or "2023-06-01"

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
        self._models_fetching = False
        self.main.set_models_refreshing(False)
        if error or models is None:
            msg = tr("models.failed", err=error or "unknown")
            self.main.set_models_status(msg)
            self.main.set_status(msg, busy=False)
            return
        assert isinstance(models, list)
        self.main.set_models_list(models, keep_current=True)
        msg = tr("models.ok", n=len(models))
        self.main.set_models_status(msg)
        self.main.set_status(msg, busy=False)

    def on_monitor_status(self, msg: str) -> None:
        if self.pipeline.busy:
            return
        # "畫面未變化" etc. are status only — main window, not overlay content
        busy = any(
            key in msg
            for key in ("等待畫面穩定", "畫面仍在變化", "畫面已穩定", "開始處理")
        )
        self.main.set_status(msg, busy=busy)
        # Do not push idle / unchanged monitor messages onto overlay text area

    def _on_pipeline_busy(self, busy: bool) -> None:
        self.main.set_busy(busy)
        if busy:
            self.main.set_status("處理中…", busy=True)

    def on_progress(self, msg: str) -> None:
        self.main.set_status(msg, busy=True)
        if self.overlay.isVisible():
            self.overlay.set_status(msg)

    def on_pipeline_finished(self, result: object) -> None:
        assert isinstance(result, PipelineResult)

        # Unchanged text / skipped: status only — never rewrite overlay content
        if result.skipped:
            msg = result.status_message or "文字未變化"
            self.main.set_status(msg, busy=False)
            if self.overlay.isVisible():
                self.overlay.set_status(msg)
            return

        # Show results on overlay when it is open, or was open when capture started
        if self._overlay_was_visible or self.overlay.isVisible():
            self.overlay.show()

        if result.error and not result.source_text:
            self.main.set_status(result.error, busy=False)
            self.main.set_result_text(result.error)
            self.overlay.set_content(translation=result.error, status="錯誤")
            # Do not overwrite OBS subtitle on hard errors
            return
        if result.error and result.source_text and not result.translated_text:
            self.main.set_status(result.error, busy=False)
            self.main.set_result_text(
                f"【原文】\n{result.source_text}\n\n（{result.error}）"
            )
            self.overlay.set_content(
                source=result.source_text,
                translation=result.error,
                status=result.error,
            )
            return

        if result.ocr_only:
            text = f"【原文 / OCR】\n{result.source_text}\n\n（未設定 API Key，僅 OCR）"
            self.main.set_result_text(text)
            self.main.set_status("OCR 完成（未翻譯）", busy=False)
            self.overlay.set_content(
                source=result.source_text,
                translation=result.source_text,
                status="僅 OCR（未設定 LLM）",
            )
            self.obs.publish(
                source=result.source_text,
                translation=result.source_text,
                status="ocr_only",
            )
            return

        cache_tag = "（快取）" if result.from_cache else ""
        text = (
            f"【原文】\n{result.source_text}\n\n"
            f"【譯文】{cache_tag}\n{result.translated_text}"
        )
        self.main.set_result_text(text)
        self.main.set_status("完成" + cache_tag, busy=False)
        self.overlay.set_content(
            source=result.source_text,
            translation=result.translated_text,
            status="完成" + cache_tag,
        )
        self.obs.publish(
            source=result.source_text,
            translation=result.translated_text,
            status="ok" + ("_cache" if result.from_cache else ""),
        )

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
