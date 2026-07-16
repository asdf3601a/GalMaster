"""Main settings / control window."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.capture.windows import WindowInfo
from app.config import LANGUAGE_CHOICES, AppConfig
from app.ocr.base import DEFAULT_OCR_ENGINE, normalize_ocr_engine
from app.translate.providers import PROVIDER_PRESETS, get_preset
from app.ui.styles import MAIN_STYLE
from app.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox


class MainWindow(QMainWindow):
    select_region_clicked = Signal()
    refresh_windows_clicked = Signal()
    bind_window_changed = Signal(int, str)
    translate_now_clicked = Signal()
    auto_monitor_toggled = Signal(bool)
    apply_settings_clicked = Signal()
    save_settings_clicked = Signal()
    cancel_settings_clicked = Signal()
    toggle_overlay_clicked = Signal()
    clear_cache_clicked = Signal()
    exit_requested = Signal()
    hide_to_tray_requested = Signal()

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.setWindowTitle("GalMaster")
        self.setStyleSheet(MAIN_STYLE)
        self._cfg = deepcopy(cfg)  # last applied snapshot
        self._windows: list[WindowInfo] = []
        self._force_close = False
        self._applying_preset = False
        self._loading_ui = False
        self._dirty = False
        self._monitor_running = bool(cfg.auto_monitor)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setSpacing(8)
        outer.setContentsMargins(10, 10, 10, 10)

        outer.addWidget(self._build_top_bar())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        body = QWidget()
        root = QVBoxLayout(body)
        root.setSpacing(8)
        root.setContentsMargins(0, 0, 0, 0)

        root.addWidget(self._build_capture_group(cfg))
        root.addWidget(self._build_lang_group(cfg))
        root.addWidget(self._build_llm_group(cfg))
        root.addWidget(self._build_display_group(cfg))
        root.addWidget(self._build_result_group())
        root.addWidget(self._build_footer())
        root.addStretch(0)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage("就緒 — 框選區域後按熱鍵翻譯")
        self._apply_window_geometry(cfg)
        self._set_monitor_buttons(self._monitor_running)
        self._set_dirty(False)

    # ------------------------------------------------------------------ top bar

    def _build_top_bar(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("topBar")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 4)
        col.setSpacing(6)

        self.work_status = QLabel("就緒 — 框選區域後按熱鍵翻譯")
        self.work_status.setObjectName("workStatus")
        self.work_status.setWordWrap(True)
        self.work_status.setProperty("busy", "false")
        col.addWidget(self.work_status)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.btn_start_monitor = QPushButton("開始監控")
        self.btn_start_monitor.setToolTip("開始自動監控（畫面變化時翻譯）")
        self.btn_start_monitor.clicked.connect(lambda: self.auto_monitor_toggled.emit(True))

        self.btn_stop_monitor = QPushButton("停止監控")
        self.btn_stop_monitor.setObjectName("secondary")
        self.btn_stop_monitor.setToolTip("停止自動監控")
        self.btn_stop_monitor.clicked.connect(lambda: self.auto_monitor_toggled.emit(False))

        self.settings_hint = QLabel("修改後請套用或儲存")
        self.settings_hint.setObjectName("hint")

        self.btn_apply = QPushButton("套用")
        self.btn_apply.setObjectName("secondary")
        self.btn_apply.setToolTip("套用到執行中程式（不寫入檔案）")
        self.btn_apply.clicked.connect(self.apply_settings_clicked.emit)

        self.btn_save = QPushButton("儲存")
        self.btn_save.setToolTip("套用並寫入 config.json")
        self.btn_save.clicked.connect(self.save_settings_clicked.emit)

        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("secondary")
        self.btn_cancel.setToolTip("還原為上次套用／儲存的設定")
        self.btn_cancel.clicked.connect(self.cancel_settings_clicked.emit)

        row.addWidget(self.btn_start_monitor)
        row.addWidget(self.btn_stop_monitor)
        row.addWidget(self.settings_hint, 1)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_cancel)
        col.addLayout(row)
        return wrap

    def _set_monitor_buttons(self, running: bool) -> None:
        self._monitor_running = running
        self.btn_start_monitor.setEnabled(not running)
        self.btn_stop_monitor.setEnabled(running)

    def set_monitor_running(self, running: bool) -> None:
        """Sync Start/Stop button enable state with RegionMonitor."""
        self._set_monitor_buttons(bool(running))

    # ------------------------------------------------------------------ builders

    def _build_capture_group(self, cfg: AppConfig) -> QGroupBox:
        cap = QGroupBox("擷取")
        cap_l = QVBoxLayout(cap)
        cap_l.setSpacing(6)

        row = QHBoxLayout()
        self.window_combo = NoWheelComboBox()
        self.window_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.window_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.addWidget(self.window_combo, 1)
        self.btn_refresh = QPushButton("重新整理")
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.clicked.connect(self._on_refresh)
        row.addWidget(self.btn_refresh)
        cap_l.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_region = QPushButton("框選 OCR 區域")
        self.btn_region.clicked.connect(self.select_region_clicked.emit)
        row2.addWidget(self.btn_region)
        self.btn_translate = QPushButton("立即翻譯")
        self.btn_translate.clicked.connect(self.translate_now_clicked.emit)
        row2.addWidget(self.btn_translate)
        cap_l.addLayout(row2)

        self.region_label = QLabel()
        self.region_label.setObjectName("hint")
        self.region_label.setWordWrap(True)
        self._update_region_label()
        cap_l.addWidget(self.region_label)

        mon = QFormLayout()
        mon.setSpacing(6)
        mon.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        mon.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        mon.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)

        self.stable_ms_spin = NoWheelSpinBox()
        self.stable_ms_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.stable_ms_spin.setRange(0, 10000)
        self.stable_ms_spin.setSingleStep(100)
        self.stable_ms_spin.setSuffix(" ms")
        stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
        if not getattr(cfg, "monitor_wait_stable", True):
            stable_ms = 0
        self.stable_ms_spin.setValue(max(0, stable_ms))
        self.stable_ms_spin.setToolTip(
            "畫面安靜（低於變化閾值）持續多久後才開始 OCR。\n"
            "0 = 偵測到變化立即 OCR（不等待穩定）。\n"
            "台詞淡入較慢可調高（例如 1000–2000 ms）。"
        )
        self.stable_ms_spin.valueChanged.connect(self._mark_dirty)
        mon.addRow("穩定時間", self.stable_ms_spin)

        self.interval_spin = NoWheelSpinBox()
        self.interval_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.interval_spin.setRange(0, 5000)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setValue(int(cfg.monitor_interval_ms or 0))
        self.interval_spin.setToolTip(
            "輪詢畫面的間隔。\n"
            "0 = 使用預設 600 ms；實際下限約 200 ms。"
        )
        self.interval_spin.valueChanged.connect(self._mark_dirty)
        mon.addRow("間隔", self.interval_spin)

        self.threshold_spin = NoWheelDoubleSpinBox()
        self.threshold_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.threshold_spin.setRange(0.0, 0.5)
        self.threshold_spin.setSingleStep(0.005)
        self.threshold_spin.setDecimals(3)
        self.threshold_spin.setValue(float(cfg.monitor_diff_threshold))
        self.threshold_spin.setToolTip(
            "畫面差異超過此值視為「有變化」。\n"
            "0 = 極敏感（使用最小門檻，僅在有實際像素差異時觸發）。"
        )
        self.threshold_spin.valueChanged.connect(self._mark_dirty)
        mon.addRow("變化閾值", self.threshold_spin)

        cap_l.addLayout(mon)

        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        return cap

    def _build_lang_group(self, cfg: AppConfig) -> QGroupBox:
        lang = QGroupBox("語言")
        row = QHBoxLayout(lang)
        self.source_combo = NoWheelComboBox()
        self.target_combo = NoWheelComboBox()
        self.source_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.target_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for code, name in LANGUAGE_CHOICES:
            self.source_combo.addItem(name, code)
            if code != "auto":
                self.target_combo.addItem(name, code)
        self._set_combo(self.source_combo, cfg.source_lang)
        self._set_combo(self.target_combo, cfg.target_lang)
        self.source_combo.currentIndexChanged.connect(self._mark_dirty)
        self.target_combo.currentIndexChanged.connect(self._mark_dirty)
        row.addWidget(QLabel("來源"))
        row.addWidget(self.source_combo, 1)
        row.addWidget(QLabel("→"))
        row.addWidget(QLabel("目標"))
        row.addWidget(self.target_combo, 1)
        return lang

    def _build_llm_group(self, cfg: AppConfig) -> QGroupBox:
        llm = QGroupBox("LLM API（可選）")
        form = QFormLayout(llm)
        form.setSpacing(6)
        form.setContentsMargins(8, 10, 8, 8)

        self.llm_optional_hint = QLabel("未填 API Key 時只做 OCR，不呼叫翻譯。")
        self.llm_optional_hint.setObjectName("hint")
        self.llm_optional_hint.setWordWrap(True)
        form.addRow(self.llm_optional_hint)

        self.provider_combo = NoWheelComboBox()
        self.provider_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for p in PROVIDER_PRESETS:
            self.provider_combo.addItem(p.label, p.id)
        self._set_combo(self.provider_combo, cfg.api_provider)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

        self.api_meta_label = QLabel("")
        self.api_meta_label.setObjectName("hint")
        self.api_meta_label.setWordWrap(True)

        self.api_key_edit = QLineEdit(cfg.api_key)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("可選 — 留空 = 只 OCR")
        self.api_key_edit.textChanged.connect(self._mark_dirty)

        self.base_url_edit = QLineEdit(cfg.base_url)
        self.base_url_edit.setPlaceholderText("https://…")
        self.base_url_edit.textChanged.connect(self._mark_dirty)

        self.model_edit = QLineEdit(cfg.model)
        self.model_edit.textChanged.connect(self._mark_dirty)

        self.prompt_edit = QLineEdit(cfg.custom_prompt)
        self.prompt_edit.setPlaceholderText("可選：額外翻譯指示")
        self.prompt_edit.textChanged.connect(self._mark_dirty)

        form.addRow("服務預設", self.provider_combo)
        form.addRow("", self.api_meta_label)
        form.addRow("API Key", self.api_key_edit)
        form.addRow("Base URL", self.base_url_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("補充指示", self.prompt_edit)

        self.context_spin = NoWheelSpinBox()
        self.context_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.context_spin.setRange(0, 20)
        self.context_spin.setValue(int(getattr(cfg, "context_history_size", 3) or 0))
        self.context_spin.setToolTip(
            "翻譯時帶入最近幾則 OCR/譯文作為上下文（sliding window）。\n"
            "0 = 只翻當前這句；3–5 適合角色名／代名詞連貫。"
        )
        self.context_spin.valueChanged.connect(self._mark_dirty)
        form.addRow("上下文則數", self.context_spin)

        self._sync_api_meta_from_provider(apply_defaults=False)
        return llm

    def _build_display_group(self, cfg: AppConfig) -> QGroupBox:
        ov = QGroupBox("顯示 / 熱鍵 / Overlay")
        form = QFormLayout(ov)
        form.setSpacing(6)

        self.hotkey_edit = QLineEdit(cfg.hotkey)
        self.hotkey_edit.textChanged.connect(self._mark_dirty)

        self.ocr_combo = NoWheelComboBox()
        self.ocr_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ocr_combo.addItem("OneOCR（剪取工具離線模型）", "oneocr")
        self.ocr_combo.addItem("Manga OCR", "manga")
        self.ocr_combo.addItem("RapidOCR", "rapid")
        self.ocr_combo.addItem("PaddleOCR", "paddle")
        self._set_combo(
            self.ocr_combo, normalize_ocr_engine(cfg.ocr_engine or DEFAULT_OCR_ENGINE)
        )
        self.ocr_combo.setToolTip(
            "OCR 引擎：\n"
            "• OneOCR：剪取工具 oneocr.dll 離線模型（推薦，中日英）。\n"
            "• Manga OCR：日文對白／遊戲字體友善。\n"
            "• RapidOCR：ONNX 輕量多語。\n"
            "• PaddleOCR：PP-OCR（較重，可選安裝）。\n"
            "首次 OneOCR 會從已安裝的剪取工具複製模型到 tools/oneocr。"
        )
        self.ocr_combo.currentIndexChanged.connect(self._mark_dirty)

        row_hot = QHBoxLayout()
        row_hot.addWidget(self.hotkey_edit, 1)
        row_hot.addWidget(QLabel("OCR"))
        row_hot.addWidget(self.ocr_combo)
        form.addRow("熱鍵", row_hot)

        ov_hint = QLabel(
            "Overlay 僅顯示譯文；透明度／字級／穿透／顯示皆在此設定（套用後生效）。"
        )
        ov_hint.setObjectName("hint")
        ov_hint.setWordWrap(True)
        form.addRow(ov_hint)

        self.opacity_spin = NoWheelDoubleSpinBox()
        self.opacity_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.opacity_spin.setRange(0.3, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(cfg.overlay_opacity)
        self.opacity_spin.setToolTip("Overlay 視窗不透明度")
        self.opacity_spin.valueChanged.connect(self._mark_dirty)
        self.font_spin = NoWheelSpinBox()
        self.font_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.font_spin.setRange(10, 36)
        self.font_spin.setValue(cfg.overlay_font_size)
        self.font_spin.setToolTip("Overlay 譯文字級")
        self.font_spin.valueChanged.connect(self._mark_dirty)

        row_ov = QHBoxLayout()
        row_ov.addWidget(QLabel("透明度"))
        row_ov.addWidget(self.opacity_spin)
        row_ov.addWidget(QLabel("字級"))
        row_ov.addWidget(self.font_spin)
        row_ov.addStretch(1)
        form.addRow("Overlay 外觀", row_ov)

        self.click_through_check = QCheckBox("滑鼠穿透（不擋遊戲點擊）")
        self.click_through_check.setChecked(cfg.overlay_click_through)
        self.click_through_check.setToolTip(
            "開啟後滑鼠會穿過 Overlay（無法在其上拖曳）。\n"
            "修改後請按「套用」或「儲存」。"
        )
        self.click_through_check.toggled.connect(self._mark_dirty)
        form.addRow("", self.click_through_check)

        row_btn = QHBoxLayout()
        self.btn_overlay = QPushButton("隱藏 Overlay")
        self.btn_overlay.setObjectName("secondary")
        self.btn_overlay.setToolTip("顯示或隱藏翻譯 Overlay（僅在主程式操作）")
        self.btn_overlay.clicked.connect(self.toggle_overlay_clicked.emit)
        self.btn_cache = QPushButton("清除快取")
        self.btn_cache.setObjectName("secondary")
        self.btn_cache.setToolTip(
            "清除翻譯快取與對話上下文歷史。\n"
            "快取會依原文＋上下文窗口產生金鑰；清除後下次會重新呼叫 LLM。"
        )
        self.btn_cache.clicked.connect(self.clear_cache_clicked.emit)
        row_btn.addWidget(self.btn_overlay)
        row_btn.addWidget(self.btn_cache)
        form.addRow(row_btn)
        return ov

    def _build_result_group(self) -> QGroupBox:
        res = QGroupBox("最近結果")
        res_l = QVBoxLayout(res)
        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setMinimumHeight(72)
        self.result_view.setMaximumHeight(140)
        res_l.addWidget(self.result_view)
        return res

    def _build_footer(self) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        self.btn_exit = QPushButton("結束程式")
        self.btn_exit.setObjectName("danger")
        self.btn_exit.setToolTip("結束 GalMaster（主視窗 + Overlay）")
        self.btn_exit.clicked.connect(self.exit_requested.emit)
        row.addWidget(self.btn_exit)
        wrap = QWidget()
        wrap.setLayout(row)
        return wrap

    def _apply_window_geometry(self, cfg: AppConfig) -> None:
        screen = QGuiApplication.primaryScreen()
        avail_h = 800
        avail_w = 500
        if screen:
            geo = screen.availableGeometry()
            avail_h = geo.height()
            avail_w = geo.width()

        max_h = max(420, int(avail_h * 0.85))
        max_w = max(360, int(avail_w * 0.5))
        w = min(cfg.main_window_w or 440, max_w)
        h = min(cfg.main_window_h or 560, max_h)
        if h > max_h:
            h = max_h
        if cfg.main_window_h >= 640:
            h = min(560, max_h)
        self.resize(max(380, w), max(420, h))
        self.setMinimumSize(360, 360)
        self.move(cfg.main_window_x, cfg.main_window_y)

    # ------------------------------------------------------------------ config

    def force_close(self) -> None:
        self._force_close = True
        self.close()

    @staticmethod
    def _set_combo(combo, data: str) -> None:
        idx = combo.findData(data)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.count() > 0:
            combo.setCurrentIndex(0)

    def _update_region_label(self) -> None:
        c = self._cfg
        if c.has_region:
            bound = c.bound_title or (
                f"hwnd={c.bound_hwnd}" if c.bound_hwnd else "螢幕座標"
            )
            self.region_label.setText(
                f"區域 ({c.region_x},{c.region_y}) {c.region_w}×{c.region_h} · {bound}"
            )
        else:
            self.region_label.setText("尚未框選區域")

    def set_config(self, cfg: AppConfig) -> None:
        """Replace applied snapshot and reload UI (e.g. Cancel / external update)."""
        self._cfg = deepcopy(cfg)
        self.load_from_config(cfg)
        self._update_region_label()

    def load_from_config(self, cfg: AppConfig) -> None:
        """Fill form fields from cfg without marking dirty."""
        self._loading_ui = True
        try:
            self._set_combo(self.provider_combo, cfg.api_provider)
            self._sync_api_meta_from_provider(apply_defaults=False)
            self.api_key_edit.setText(cfg.api_key or "")
            self.base_url_edit.setText(cfg.base_url or "")
            self.model_edit.setText(cfg.model or "")
            self.prompt_edit.setText(cfg.custom_prompt or "")
            self.context_spin.setValue(int(getattr(cfg, "context_history_size", 3) or 0))
            self._set_combo(self.source_combo, cfg.source_lang)
            self._set_combo(self.target_combo, cfg.target_lang)
            self.hotkey_edit.setText(cfg.hotkey or "Ctrl+Shift+T")
            self.opacity_spin.setValue(cfg.overlay_opacity)
            self.font_spin.setValue(cfg.overlay_font_size)
            self.click_through_check.setChecked(cfg.overlay_click_through)
            self._set_combo(
                self.ocr_combo,
                normalize_ocr_engine(cfg.ocr_engine or DEFAULT_OCR_ENGINE),
            )
            stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
            if not getattr(cfg, "monitor_wait_stable", True):
                stable_ms = 0
            self.stable_ms_spin.setValue(max(0, stable_ms))
            self.interval_spin.setValue(int(cfg.monitor_interval_ms or 0))
            self.threshold_spin.setValue(float(cfg.monitor_diff_threshold))
        finally:
            self._loading_ui = False
        self._set_dirty(False)

    def collect_config(self) -> AppConfig:
        """Read form into a new AppConfig (keeps region/geometry from snapshot + window)."""
        c = deepcopy(self._cfg)
        pid = self.provider_combo.currentData() or "xai"
        c.api_provider = pid
        preset = get_preset(pid)
        c.api_protocol = preset.protocol if preset else (c.api_protocol or "openai")
        c.api_key = self.api_key_edit.text().strip()
        c.base_url = self.base_url_edit.text().strip() or (
            preset.base_url if preset else "https://api.x.ai/v1"
        )
        c.model = self.model_edit.text().strip() or c.model
        c.custom_prompt = self.prompt_edit.text().strip()
        c.context_history_size = int(self.context_spin.value())
        c.source_lang = self.source_combo.currentData() or "ja"
        c.target_lang = self.target_combo.currentData() or "zh-Hant"
        c.hotkey = self.hotkey_edit.text().strip() or "Ctrl+Shift+T"
        c.overlay_opacity = float(self.opacity_spin.value())
        c.overlay_font_size = int(self.font_spin.value())
        c.overlay_click_through = self.click_through_check.isChecked()
        c.ocr_engine = normalize_ocr_engine(
            self.ocr_combo.currentData() or DEFAULT_OCR_ENGINE
        )
        c.auto_monitor = bool(self._monitor_running)
        c.monitor_stable_ms = int(self.stable_ms_spin.value())
        c.monitor_wait_stable = c.monitor_stable_ms > 0
        c.monitor_interval_ms = int(self.interval_spin.value())
        c.monitor_diff_threshold = float(self.threshold_spin.value())
        geo = self.geometry()
        c.main_window_x, c.main_window_y = geo.x(), geo.y()
        c.main_window_w, c.main_window_h = geo.width(), geo.height()
        return c

    def mark_applied(self, cfg: AppConfig) -> None:
        """Called after successful Apply/Save."""
        self._cfg = deepcopy(cfg)
        self._set_dirty(False)
        self._update_region_label()

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if dirty:
            self.settings_hint.setText("有未套用的變更")
            self.setWindowTitle("GalMaster *")
        else:
            self.settings_hint.setText("修改後請套用或儲存")
            self.setWindowTitle("GalMaster")

    def _mark_dirty(self, *_args) -> None:
        if self._loading_ui or self._applying_preset:
            return
        self._set_dirty(True)

    def _on_provider_changed(self, _idx: int = 0) -> None:
        if self._loading_ui or self._applying_preset:
            return
        self._sync_api_meta_from_provider(apply_defaults=True)
        self._mark_dirty()

    def _sync_api_meta_from_provider(self, *, apply_defaults: bool) -> None:
        pid = self.provider_combo.currentData() or "xai"
        preset = get_preset(pid)
        if not preset:
            self.api_meta_label.setText("")
            return

        if preset.protocol == "openai":
            style = "OpenAI Compatible · POST {base}/chat/completions"
        else:
            style = "Anthropic Compatible · POST {base}/v1/messages"

        bits = [style]
        if preset.hint:
            bits.append(preset.hint)
        self.api_meta_label.setText(" · ".join(bits))

        if preset.env_keys:
            self.api_key_edit.setPlaceholderText(" / ".join(preset.env_keys))

        if apply_defaults:
            self._applying_preset = True
            try:
                self.base_url_edit.setText(preset.base_url)
                if preset.model:
                    self.model_edit.setText(preset.model)
            finally:
                self._applying_preset = False

    def set_windows(self, windows: list[WindowInfo]) -> None:
        self._windows = windows
        current_hwnd = self._cfg.bound_hwnd
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        self.window_combo.addItem("（不綁定 — 螢幕座標）", 0)
        select = 0
        for i, w in enumerate(windows, start=1):
            self.window_combo.addItem(w.title[:80], w.hwnd)
            if w.hwnd == current_hwnd:
                select = i
        self.window_combo.setCurrentIndex(select)
        self.window_combo.blockSignals(False)

    def set_region_info(self, cfg: AppConfig) -> None:
        # Region is operational state — update applied snapshot fields
        self._cfg.region_x = cfg.region_x
        self._cfg.region_y = cfg.region_y
        self._cfg.region_w = cfg.region_w
        self._cfg.region_h = cfg.region_h
        self._cfg.region_abs_x = int(getattr(cfg, "region_abs_x", 0) or 0)
        self._cfg.region_abs_y = int(getattr(cfg, "region_abs_y", 0) or 0)
        self._cfg.region_abs_w = int(getattr(cfg, "region_abs_w", 0) or 0)
        self._cfg.region_abs_h = int(getattr(cfg, "region_abs_h", 0) or 0)
        self._cfg.bound_hwnd = cfg.bound_hwnd
        self._cfg.bound_title = cfg.bound_title
        self._update_region_label()

    def mark_runtime_dirty(self) -> None:
        """Mark form dirty after operational changes that should be Saved (e.g. monitor)."""
        self._set_dirty(True)

    def set_result_text(self, text: str) -> None:
        self.result_view.setPlainText(text)

    def set_status(self, msg: str, *, busy: bool | None = None) -> None:
        text = msg or ""
        self.statusBar().showMessage(text)
        if hasattr(self, "work_status") and self.work_status is not None:
            self.work_status.setText(text if text else "就緒")
            if busy is not None:
                self.work_status.setProperty("busy", "true" if busy else "false")
                self.work_status.style().unpolish(self.work_status)
                self.work_status.style().polish(self.work_status)

    def set_busy(self, busy: bool) -> None:
        self.btn_translate.setEnabled(not busy)
        self.btn_region.setEnabled(not busy)
        if busy and hasattr(self, "work_status"):
            cur = self.work_status.text() or "處理中…"
            self.set_status(cur, busy=True)
        elif not busy and hasattr(self, "work_status"):
            self.work_status.setProperty("busy", "false")
            self.work_status.style().unpolish(self.work_status)
            self.work_status.style().polish(self.work_status)

    def set_overlay_button_state(self, visible: bool) -> None:
        if visible:
            self.btn_overlay.setText("隱藏 Overlay")
        else:
            self.btn_overlay.setText("顯示 Overlay")

    def _on_refresh(self) -> None:
        self.refresh_windows_clicked.emit()

    def _on_window_selected(self, _idx: int) -> None:
        # Binding is immediate operational action
        hwnd = int(self.window_combo.currentData() or 0)
        title = self.window_combo.currentText() if hwnd else ""
        if hwnd == 0:
            title = ""
        self.bind_window_changed.emit(hwnd, title)

    def show_error(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    def show_info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._force_close:
            event.accept()
            return
        event.ignore()
        self.hide_to_tray_requested.emit()
