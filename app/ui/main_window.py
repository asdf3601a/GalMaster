"""Main settings / control window."""

from __future__ import annotations

from copy import deepcopy

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication, QImage, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
from app.config import LANGUAGE_CHOICES, AppConfig, normalize_hex_color
from app.i18n import available_languages, set_language, tr
from app.ocr.base import DEFAULT_OCR_ENGINE, normalize_ocr_engine
from app.translate.providers import PROVIDER_PRESETS, get_preset
from app.ui.styles import MAIN_STYLE
from app.ui.widgets import (
    ColorButton,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelFontComboBox,
    NoWheelSpinBox,
)


def _optional_float_row(
    *,
    enabled: bool,
    spin: NoWheelDoubleSpinBox,
) -> float | None:
    return float(spin.value()) if enabled else None


def _optional_int_row(*, enabled: bool, spin: NoWheelSpinBox) -> int | None:
    return int(spin.value()) if enabled else None


def _cfg_int(cfg: AppConfig, name: str, default: int) -> int:
    """Read int config field; 0 is valid (do not use `or default`)."""
    val = getattr(cfg, name, default)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


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
    copy_obs_url_clicked = Signal()
    # role: "translate" | "vlm"
    refresh_models_clicked = Signal(str)

    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        set_language(getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant")
        self.setWindowTitle(tr("app.title"))
        # App-level stylesheet is set in main.py; keep a window copy for isolation
        # when MainWindow is constructed in tests without going through main().
        if not self.styleSheet():
            self.setStyleSheet(MAIN_STYLE)
        self._cfg = deepcopy(cfg)  # last applied snapshot
        self._windows: list[WindowInfo] = []
        self._force_close = False
        self._applying_preset = False
        self._loading_ui = False
        self._dirty = False
        self._monitor_running = bool(cfg.auto_monitor)
        self._pipeline_busy = False
        self._overlay_visible = True
        self._obs_status_key = "off"  # off | on | err
        self._obs_status_url = ""
        self._obs_status_err = ""
        # Recent result log (one concise line per turn); newest last
        self._result_lines: list[str] = []

        # form-row labels for retranslate: i18n_key -> one or more QLabel (no key collisions)
        self._form_labels: dict[str, list[QLabel]] = {}

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

        # Pipeline order: Detect → Capture → Process → Present → App
        root.addWidget(self._build_detect_group(cfg))
        root.addWidget(self._build_capture_group(cfg))
        root.addWidget(self._build_process_group(cfg))
        root.addWidget(self._build_recognize_group(cfg))
        root.addWidget(self._build_llm_group(cfg))
        root.addWidget(self._build_overlay_group(cfg))
        root.addWidget(self._build_obs_group(cfg))
        root.addWidget(self._build_result_group())
        root.addWidget(self._build_app_group(cfg))
        root.addWidget(self._build_footer())
        root.addStretch(0)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.setCentralWidget(central)

        self.statusBar().showMessage(tr("status.ready"))
        self._apply_window_geometry(cfg)
        self._set_monitor_buttons(self._monitor_running)
        self._set_dirty(False)
        self._on_pipeline_mode_changed()
        self.retranslate()

    # ------------------------------------------------------------------ top bar

    def _build_top_bar(self) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("topBar")
        col = QVBoxLayout(wrap)
        col.setContentsMargins(0, 0, 0, 4)
        col.setSpacing(6)

        self.work_status = QLabel(tr("status.ready"))
        self.work_status.setObjectName("workStatus")
        self.work_status.setWordWrap(True)
        self.work_status.setProperty("busy", "false")
        col.addWidget(self.work_status)

        row = QHBoxLayout()
        row.setSpacing(6)

        self.btn_start_monitor = QPushButton()
        self.btn_start_monitor.clicked.connect(
            lambda: self.auto_monitor_toggled.emit(True)
        )

        self.btn_stop_monitor = QPushButton()
        self.btn_stop_monitor.setObjectName("secondary")
        self.btn_stop_monitor.clicked.connect(
            lambda: self.auto_monitor_toggled.emit(False)
        )

        self.settings_hint = QLabel()
        self.settings_hint.setObjectName("hint")

        self.btn_apply = QPushButton()
        self.btn_apply.setObjectName("secondary")
        self.btn_apply.clicked.connect(self.apply_settings_clicked.emit)

        self.btn_save = QPushButton()
        self.btn_save.clicked.connect(self.save_settings_clicked.emit)

        self.btn_cancel = QPushButton()
        self.btn_cancel.setObjectName("secondary")
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
        # Start: only when not monitoring (busy state may still disable it).
        if not running:
            # Leave enable decision to set_busy if pipeline is mid-job.
            if not getattr(self, "_pipeline_busy", False):
                self.btn_start_monitor.setEnabled(True)
        else:
            self.btn_start_monitor.setEnabled(False)
        # Stop must never wait on OCR/LLM — always clickable while monitoring.
        self.btn_stop_monitor.setEnabled(running)

    def set_monitor_running(self, running: bool) -> None:
        """Sync Start/Stop button enable state with RegionMonitor."""
        self._set_monitor_buttons(bool(running))

    # ------------------------------------------------------------------ builders

    def _add_form_label(self, form: QFormLayout, key: str, field: QWidget) -> None:
        """Add a form row; *key* is an i18n id. Multiple rows may share the same key."""
        lab = QLabel()
        self._form_labels.setdefault(key, []).append(lab)
        form.addRow(lab, field)

    def _expand_field(self, w: QWidget) -> None:
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        w.setMinimumWidth(0)

    def _build_detect_group(self, cfg: AppConfig) -> QGroupBox:
        """Detect stage: bind target + region + monitor timing."""
        self.detect_group = QGroupBox()
        # Keep legacy alias used by retranslate / external refs
        self.cap_group = self.detect_group
        det_l = QVBoxLayout(self.detect_group)
        det_l.setSpacing(6)

        row = QHBoxLayout()
        self.window_combo = NoWheelComboBox()
        self.window_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.window_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row.addWidget(self.window_combo, 1)
        self.btn_refresh = QPushButton()
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.clicked.connect(self._on_refresh)
        row.addWidget(self.btn_refresh)
        det_l.addLayout(row)

        self.btn_region = QPushButton()
        self.btn_region.clicked.connect(self.select_region_clicked.emit)
        det_l.addWidget(self.btn_region)

        self.region_label = QLabel()
        self.region_label.setObjectName("hint")
        self.region_label.setWordWrap(True)
        self._update_region_label()
        det_l.addWidget(self.region_label)

        mon = QFormLayout()
        mon.setSpacing(6)
        mon.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        mon.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        mon.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.stable_ms_spin = NoWheelSpinBox()
        self.stable_ms_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.stable_ms_spin.setRange(0, 10000)
        self.stable_ms_spin.setSingleStep(100)
        self.stable_ms_spin.setSuffix(" ms")
        self.stable_ms_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._expand_field(self.stable_ms_spin)
        stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
        if not getattr(cfg, "monitor_wait_stable", True):
            stable_ms = 0
        self.stable_ms_spin.setValue(max(0, stable_ms))
        self.stable_ms_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(mon, "label.stable_ms", self.stable_ms_spin)

        self.interval_spin = NoWheelSpinBox()
        self.interval_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.interval_spin.setRange(0, 5000)
        self.interval_spin.setSingleStep(50)
        self.interval_spin.setSuffix(" ms")
        self.interval_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._expand_field(self.interval_spin)
        self.interval_spin.setValue(int(cfg.monitor_interval_ms or 0))
        self.interval_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(mon, "label.interval", self.interval_spin)

        self.cooldown_spin = NoWheelSpinBox()
        self.cooldown_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cooldown_spin.setRange(0, 30000)
        self.cooldown_spin.setSingleStep(100)
        self.cooldown_spin.setSuffix(" ms")
        self.cooldown_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._expand_field(self.cooldown_spin)
        self.cooldown_spin.setValue(int(getattr(cfg, "monitor_cooldown_ms", 1200) or 0))
        self.cooldown_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(mon, "label.cooldown", self.cooldown_spin)

        self.threshold_spin = NoWheelDoubleSpinBox()
        self.threshold_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.threshold_spin.setRange(0.0, 0.5)
        self.threshold_spin.setSingleStep(0.005)
        self.threshold_spin.setDecimals(3)
        self.threshold_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._expand_field(self.threshold_spin)
        self.threshold_spin.setValue(float(cfg.monitor_diff_threshold))
        self.threshold_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(mon, "label.threshold", self.threshold_spin)

        self.monitor_hint = QLabel()
        self.monitor_hint.setObjectName("hint")
        self.monitor_hint.setWordWrap(True)
        mon.addRow(self.monitor_hint)

        det_l.addLayout(mon)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        return self.detect_group

    def _build_capture_group(self, cfg: AppConfig) -> QGroupBox:
        """Capture stage: method, force translate, preview."""
        self.capture_group = QGroupBox()
        cap_l = QVBoxLayout(self.capture_group)
        cap_l.setSpacing(6)

        form = QFormLayout()
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.capture_method_combo = NoWheelComboBox()
        self.capture_method_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._expand_field(self.capture_method_combo)
        for code in ("auto", "wgc", "bitblt"):
            self.capture_method_combo.addItem(code, code)
        self._set_combo(
            self.capture_method_combo,
            str(getattr(cfg, "window_capture_method", "auto") or "auto"),
        )
        self.capture_method_combo.currentIndexChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.capture_method", self.capture_method_combo)
        cap_l.addLayout(form)

        self.btn_translate = QPushButton()
        self.btn_translate.clicked.connect(self.translate_now_clicked.emit)
        cap_l.addWidget(self.btn_translate)

        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("hint")
        cap_l.addWidget(self.lbl_preview)

        self.preview_label = QLabel()
        self.preview_label.setObjectName("previewBox")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(120)
        self.preview_label.setMaximumHeight(160)
        self.preview_label.setText(tr("hint.preview_empty"))
        self.preview_label.setScaledContents(False)
        cap_l.addWidget(self.preview_label)
        return self.capture_group

    def _build_process_group(self, cfg: AppConfig) -> QGroupBox:
        """Process header: pipeline mode, languages, process buffer."""
        self.process_group = QGroupBox()
        # Legacy alias for language row styling / retranslate fallbacks
        self.lang_group = self.process_group
        form = QFormLayout(self.process_group)
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.pipeline_mode_combo = NoWheelComboBox()
        self.pipeline_mode_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._expand_field(self.pipeline_mode_combo)
        self.pipeline_mode_combo.addItem("", "ocr")
        self.pipeline_mode_combo.addItem("", "vlm")
        self.pipeline_mode_combo.addItem("", "vlm_ocr")
        self._set_combo(
            self.pipeline_mode_combo,
            (getattr(cfg, "pipeline_mode", "ocr") or "ocr"),
        )
        self.pipeline_mode_combo.currentIndexChanged.connect(self._mark_dirty)
        self.pipeline_mode_combo.currentIndexChanged.connect(
            self._on_pipeline_mode_changed
        )
        self._add_form_label(form, "label.pipeline_mode", self.pipeline_mode_combo)

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
        self.lbl_source = QLabel()
        self.lbl_target = QLabel()
        lang_row = QHBoxLayout()
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(6)
        lang_row.addWidget(self.lbl_source)
        lang_row.addWidget(self.source_combo, 1)
        lang_row.addWidget(QLabel("→"))
        lang_row.addWidget(self.lbl_target)
        lang_row.addWidget(self.target_combo, 1)
        lang_wrap = QWidget()
        lang_wrap.setLayout(lang_row)
        form.addRow(lang_wrap)

        self.buffer_spin = NoWheelSpinBox()
        self.buffer_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.buffer_spin.setRange(1, 16)
        self.buffer_spin.setSingleStep(1)
        self.buffer_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._expand_field(self.buffer_spin)
        try:
            from app.config import clamp_pipeline_buffer_size

            buf_n = clamp_pipeline_buffer_size(getattr(cfg, "pipeline_buffer_size", 3))
        except Exception:
            buf_n = 3
        self.buffer_spin.setValue(buf_n)
        self.buffer_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.pipeline_buffer", self.buffer_spin)

        return self.process_group

    def _make_optional_float(
        self,
        *,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int,
        default: float,
        enabled: bool,
    ) -> tuple[QCheckBox, NoWheelDoubleSpinBox, QWidget]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        chk = QCheckBox()
        spin = NoWheelDoubleSpinBox()
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setValue(default)
        spin.setEnabled(enabled)
        chk.setChecked(enabled)
        chk.toggled.connect(spin.setEnabled)
        chk.toggled.connect(self._mark_dirty)
        spin.valueChanged.connect(self._mark_dirty)
        row.addWidget(chk)
        row.addWidget(spin, 1)
        return chk, spin, wrap

    def _make_optional_int(
        self,
        *,
        minimum: int,
        maximum: int,
        step: int,
        default: int,
        enabled: bool,
    ) -> tuple[QCheckBox, NoWheelSpinBox, QWidget]:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        chk = QCheckBox()
        spin = NoWheelSpinBox()
        spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setValue(default)
        spin.setEnabled(enabled)
        chk.setChecked(enabled)
        chk.toggled.connect(spin.setEnabled)
        chk.toggled.connect(self._mark_dirty)
        spin.valueChanged.connect(self._mark_dirty)
        row.addWidget(chk)
        row.addWidget(spin, 1)
        return chk, spin, wrap

    def _make_optional_combo(
        self,
        *,
        items: list[tuple[str, str]],
        current: str,
        enabled: bool,
    ) -> tuple[QCheckBox, NoWheelComboBox, QWidget]:
        """Checkbox + combo row, matching optional temperature/top_p style."""
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        chk = QCheckBox()
        combo = NoWheelComboBox()
        combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for text, data in items:
            combo.addItem(text, data)
        self._set_combo(combo, current)
        combo.setEnabled(enabled)
        chk.setChecked(enabled)
        chk.toggled.connect(combo.setEnabled)
        chk.toggled.connect(self._mark_dirty)
        combo.currentIndexChanged.connect(self._mark_dirty)
        row.addWidget(chk)
        row.addWidget(combo, 1)
        return chk, combo, wrap

    def _build_llm_group(self, cfg: AppConfig) -> QGroupBox:
        self.llm_group = QGroupBox()
        form = QFormLayout(self.llm_group)
        form.setSpacing(6)
        form.setContentsMargins(8, 10, 8, 8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.llm_optional_hint = QLabel()
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
        self.api_key_edit.textChanged.connect(self._mark_dirty)

        self.base_url_edit = QLineEdit(cfg.base_url)
        self.base_url_edit.setPlaceholderText("https://…")
        self.base_url_edit.textChanged.connect(self._mark_dirty)

        # Editable combo: dropdown when GET /models succeeds; always allow typing.
        self.model_combo = NoWheelComboBox()
        self.model_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if self.model_combo.lineEdit() is not None:
            self.model_combo.lineEdit().setText(cfg.model or "")
            self.model_combo.lineEdit().textChanged.connect(self._mark_dirty)
        self.model_combo.currentIndexChanged.connect(self._on_model_combo_changed)
        self._model_ids: list[str] = []

        self.btn_refresh_models = QPushButton()
        self.btn_refresh_models.setObjectName("secondary")
        self.btn_refresh_models.clicked.connect(
            lambda: self.refresh_models_clicked.emit("translate")
        )

        model_row = QHBoxLayout()
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(6)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.btn_refresh_models)
        self.model_row_wrap = QWidget()
        self.model_row_wrap.setLayout(model_row)

        self.models_status_label = QLabel("")
        self.models_status_label.setObjectName("hint")
        self.models_status_label.setWordWrap(True)

        self.prompt_edit = QLineEdit(cfg.custom_prompt)
        self.prompt_edit.textChanged.connect(self._mark_dirty)

        self._add_form_label(form, "label.provider", self.provider_combo)
        form.addRow("", self.api_meta_label)
        self._add_form_label(form, "label.api_key", self.api_key_edit)
        self._add_form_label(form, "label.base_url", self.base_url_edit)
        self._add_form_label(form, "label.model", self.model_row_wrap)
        form.addRow("", self.models_status_label)
        self._add_form_label(form, "label.custom_prompt", self.prompt_edit)

        self.context_spin = NoWheelSpinBox()
        self.context_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.context_spin.setRange(0, 20)
        self._expand_field(self.context_spin)
        self.context_spin.setValue(int(getattr(cfg, "context_history_size", 3) or 0))
        self.context_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.context", self.context_spin)

        self.max_tokens_spin = NoWheelSpinBox()
        self.max_tokens_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.max_tokens_spin.setRange(64, 128000)
        self.max_tokens_spin.setSingleStep(256)
        self._expand_field(self.max_tokens_spin)
        self.max_tokens_spin.setValue(int(getattr(cfg, "max_tokens", 2048) or 2048))
        self.max_tokens_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.max_tokens", self.max_tokens_spin)

        self.timeout_spin = NoWheelDoubleSpinBox()
        self.timeout_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.timeout_spin.setRange(5.0, 120.0)
        self.timeout_spin.setDecimals(0)
        self.timeout_spin.setSingleStep(5.0)
        self._expand_field(self.timeout_spin)
        self.timeout_spin.setValue(float(getattr(cfg, "llm_timeout_s", 30) or 30))
        self.timeout_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.llm_timeout", self.timeout_spin)

        # Advanced optional sampling
        self.adv_group = QGroupBox()
        self.adv_group.setCheckable(False)
        adv = QFormLayout(self.adv_group)
        adv.setSpacing(4)

        temp_v = getattr(cfg, "temperature", None)
        self.temp_chk, self.temp_spin, temp_w = self._make_optional_float(
            minimum=0.0,
            maximum=2.0,
            step=0.05,
            decimals=2,
            default=float(temp_v) if temp_v is not None else 0.2,
            enabled=temp_v is not None,
        )
        self._add_form_label(adv, "label.temperature", temp_w)

        topp_v = getattr(cfg, "top_p", None)
        self.topp_chk, self.topp_spin, topp_w = self._make_optional_float(
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            decimals=2,
            default=float(topp_v) if topp_v is not None else 1.0,
            enabled=topp_v is not None,
        )
        self._add_form_label(adv, "label.top_p", topp_w)

        topk_v = getattr(cfg, "top_k", None)
        self.topk_chk, self.topk_spin, topk_w = self._make_optional_int(
            minimum=1,
            maximum=200,
            step=1,
            default=int(topk_v) if topk_v is not None else 40,
            enabled=topk_v is not None and int(topk_v) > 0,
        )
        self._add_form_label(adv, "label.top_k", topk_w)

        fp_v = getattr(cfg, "frequency_penalty", None)
        self.fp_chk, self.fp_spin, fp_w = self._make_optional_float(
            minimum=-2.0,
            maximum=2.0,
            step=0.1,
            decimals=2,
            default=float(fp_v) if fp_v is not None else 0.0,
            enabled=fp_v is not None,
        )
        self._add_form_label(adv, "label.freq_penalty", fp_w)

        pp_v = getattr(cfg, "presence_penalty", None)
        self.pp_chk, self.pp_spin, pp_w = self._make_optional_float(
            minimum=-2.0,
            maximum=2.0,
            step=0.1,
            decimals=2,
            default=float(pp_v) if pp_v is not None else 0.0,
            enabled=pp_v is not None,
        )
        self._add_form_label(adv, "label.pres_penalty", pp_w)

        seed_v = getattr(cfg, "seed", None)
        self.seed_chk, self.seed_spin, seed_w = self._make_optional_int(
            minimum=0,
            maximum=2_147_483_647,
            step=1,
            default=int(seed_v) if seed_v is not None else 0,
            enabled=seed_v is not None,
        )
        self._add_form_label(adv, "label.seed", seed_w)

        raw_effort = (getattr(cfg, "reasoning_effort", "") or "").strip().lower()
        effort_enabled = raw_effort in ("none", "low", "medium", "high")
        # Match optional sampling rows: ☑ + expanding field (omit when unchecked)
        self.reasoning_chk, self.reasoning_combo, reasoning_w = (
            self._make_optional_combo(
                items=[
                    ("none", "none"),
                    ("low", "low"),
                    ("medium", "medium"),
                    ("high", "high"),
                ],
                current=raw_effort if effort_enabled else "none",
                enabled=effort_enabled,
            )
        )
        self._add_form_label(adv, "label.reasoning", reasoning_w)

        self.sampling_hint = QLabel()
        self.sampling_hint.setObjectName("hint")
        self.sampling_hint.setWordWrap(True)
        adv.addRow(self.sampling_hint)

        form.addRow(self.adv_group)

        self._sync_api_meta_from_provider(apply_defaults=False)
        return self.llm_group

    def _build_recognize_group(self, cfg: AppConfig) -> QGroupBox:
        """Recognition: local OCR panel and/or VLM panel (visibility by mode)."""
        self.recognize_group = QGroupBox()
        # Legacy alias
        self.vlm_group = self.recognize_group
        outer = QVBoxLayout(self.recognize_group)
        outer.setSpacing(6)
        outer.setContentsMargins(8, 10, 8, 8)

        self.recognize_hint = QLabel()
        self.recognize_hint.setObjectName("hint")
        self.recognize_hint.setWordWrap(True)
        outer.addWidget(self.recognize_hint)

        # --- Local OCR engine (mode=ocr only) ---
        self.local_ocr_panel = QWidget()
        local_form = QFormLayout(self.local_ocr_panel)
        local_form.setContentsMargins(0, 0, 0, 0)
        local_form.setSpacing(6)
        local_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )

        self.ocr_combo = NoWheelComboBox()
        self.ocr_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._expand_field(self.ocr_combo)
        for code in ("oneocr", "manga", "rapid", "paddle"):
            self.ocr_combo.addItem(code, code)
        self._set_combo(
            self.ocr_combo, normalize_ocr_engine(cfg.ocr_engine or DEFAULT_OCR_ENGINE)
        )
        self.ocr_combo.currentIndexChanged.connect(self._mark_dirty)
        self._add_form_label(local_form, "label.local_ocr", self.ocr_combo)
        outer.addWidget(self.local_ocr_panel)

        # --- VLM endpoint (mode=vlm | vlm_ocr) ---
        self.vlm_panel = QWidget()
        form = QFormLayout(self.vlm_panel)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.vlm_optional_hint = QLabel()
        self.vlm_optional_hint.setObjectName("hint")
        self.vlm_optional_hint.setWordWrap(True)
        form.addRow(self.vlm_optional_hint)

        self.vlm_provider_combo = NoWheelComboBox()
        self.vlm_provider_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for p in PROVIDER_PRESETS:
            self.vlm_provider_combo.addItem(p.label, p.id)
        self._set_combo(
            self.vlm_provider_combo,
            getattr(cfg, "vlm_api_provider", "xai") or "xai",
        )
        self.vlm_provider_combo.currentIndexChanged.connect(
            self._on_vlm_provider_changed
        )

        self.vlm_api_meta_label = QLabel("")
        self.vlm_api_meta_label.setObjectName("hint")
        self.vlm_api_meta_label.setWordWrap(True)

        self.vlm_api_key_edit = QLineEdit(getattr(cfg, "vlm_api_key", "") or "")
        self.vlm_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.vlm_api_key_edit.textChanged.connect(self._mark_dirty)

        self.vlm_base_url_edit = QLineEdit(getattr(cfg, "vlm_base_url", "") or "")
        self.vlm_base_url_edit.setPlaceholderText("https://…")
        self.vlm_base_url_edit.textChanged.connect(self._mark_dirty)

        self.vlm_model_combo = NoWheelComboBox()
        self.vlm_model_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.vlm_model_combo.setEditable(True)
        self.vlm_model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.vlm_model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        if self.vlm_model_combo.lineEdit() is not None:
            self.vlm_model_combo.lineEdit().setText(getattr(cfg, "vlm_model", "") or "")
            self.vlm_model_combo.lineEdit().textChanged.connect(self._mark_dirty)
        self.vlm_model_combo.currentIndexChanged.connect(
            self._on_vlm_model_combo_changed
        )
        self._vlm_model_ids: list[str] = []

        self.btn_vlm_refresh_models = QPushButton()
        self.btn_vlm_refresh_models.setObjectName("secondary")
        self.btn_vlm_refresh_models.clicked.connect(
            lambda: self.refresh_models_clicked.emit("vlm")
        )

        vlm_model_row = QHBoxLayout()
        vlm_model_row.setContentsMargins(0, 0, 0, 0)
        vlm_model_row.setSpacing(6)
        vlm_model_row.addWidget(self.vlm_model_combo, 1)
        vlm_model_row.addWidget(self.btn_vlm_refresh_models)
        self.vlm_model_row_wrap = QWidget()
        self.vlm_model_row_wrap.setLayout(vlm_model_row)

        self.vlm_models_status_label = QLabel("")
        self.vlm_models_status_label.setObjectName("hint")
        self.vlm_models_status_label.setWordWrap(True)

        self.vlm_prompt_edit = QLineEdit(getattr(cfg, "vlm_custom_prompt", "") or "")
        self.vlm_prompt_edit.textChanged.connect(self._mark_dirty)

        self._add_form_label(form, "label.provider", self.vlm_provider_combo)
        form.addRow("", self.vlm_api_meta_label)
        self._add_form_label(form, "label.api_key", self.vlm_api_key_edit)
        self._add_form_label(form, "label.base_url", self.vlm_base_url_edit)
        self._add_form_label(form, "label.model", self.vlm_model_row_wrap)
        form.addRow("", self.vlm_models_status_label)
        self._add_form_label(form, "label.custom_prompt", self.vlm_prompt_edit)

        self.vlm_max_tokens_spin = NoWheelSpinBox()
        self.vlm_max_tokens_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.vlm_max_tokens_spin.setRange(64, 128000)
        self.vlm_max_tokens_spin.setSingleStep(256)
        self._expand_field(self.vlm_max_tokens_spin)
        self.vlm_max_tokens_spin.setValue(
            int(getattr(cfg, "vlm_max_tokens", 2048) or 2048)
        )
        self.vlm_max_tokens_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.max_tokens", self.vlm_max_tokens_spin)

        self.vlm_timeout_spin = NoWheelDoubleSpinBox()
        self.vlm_timeout_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.vlm_timeout_spin.setRange(5.0, 120.0)
        self.vlm_timeout_spin.setDecimals(0)
        self.vlm_timeout_spin.setSingleStep(5.0)
        self._expand_field(self.vlm_timeout_spin)
        self.vlm_timeout_spin.setValue(float(getattr(cfg, "vlm_timeout_s", 30) or 30))
        self.vlm_timeout_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.llm_timeout", self.vlm_timeout_spin)

        self.vlm_adv_group = QGroupBox()
        self.vlm_adv_group.setCheckable(False)
        vadv = QFormLayout(self.vlm_adv_group)
        vadv.setSpacing(4)

        vtemp = getattr(cfg, "vlm_temperature", None)
        self.vlm_temp_chk, self.vlm_temp_spin, vtemp_w = self._make_optional_float(
            minimum=0.0,
            maximum=2.0,
            step=0.05,
            decimals=2,
            default=float(vtemp) if vtemp is not None else 0.2,
            enabled=vtemp is not None,
        )
        self._add_form_label(vadv, "label.temperature", vtemp_w)

        vtopp = getattr(cfg, "vlm_top_p", None)
        self.vlm_topp_chk, self.vlm_topp_spin, vtopp_w = self._make_optional_float(
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            decimals=2,
            default=float(vtopp) if vtopp is not None else 1.0,
            enabled=vtopp is not None,
        )
        self._add_form_label(vadv, "label.top_p", vtopp_w)

        vtopk = getattr(cfg, "vlm_top_k", None)
        self.vlm_topk_chk, self.vlm_topk_spin, vtopk_w = self._make_optional_int(
            minimum=1,
            maximum=200,
            step=1,
            default=int(vtopk) if vtopk is not None else 40,
            enabled=vtopk is not None and int(vtopk) > 0,
        )
        self._add_form_label(vadv, "label.top_k", vtopk_w)

        vfp = getattr(cfg, "vlm_frequency_penalty", None)
        self.vlm_fp_chk, self.vlm_fp_spin, vfp_w = self._make_optional_float(
            minimum=-2.0,
            maximum=2.0,
            step=0.1,
            decimals=2,
            default=float(vfp) if vfp is not None else 0.0,
            enabled=vfp is not None,
        )
        self._add_form_label(vadv, "label.freq_penalty", vfp_w)

        vpp = getattr(cfg, "vlm_presence_penalty", None)
        self.vlm_pp_chk, self.vlm_pp_spin, vpp_w = self._make_optional_float(
            minimum=-2.0,
            maximum=2.0,
            step=0.1,
            decimals=2,
            default=float(vpp) if vpp is not None else 0.0,
            enabled=vpp is not None,
        )
        self._add_form_label(vadv, "label.pres_penalty", vpp_w)

        vseed = getattr(cfg, "vlm_seed", None)
        self.vlm_seed_chk, self.vlm_seed_spin, vseed_w = self._make_optional_int(
            minimum=0,
            maximum=2_147_483_647,
            step=1,
            default=int(vseed) if vseed is not None else 0,
            enabled=vseed is not None,
        )
        self._add_form_label(vadv, "label.seed", vseed_w)

        vraw = (getattr(cfg, "vlm_reasoning_effort", "") or "").strip().lower()
        veff = vraw in ("none", "low", "medium", "high")
        self.vlm_reasoning_chk, self.vlm_reasoning_combo, vre_w = (
            self._make_optional_combo(
                items=[
                    ("none", "none"),
                    ("low", "low"),
                    ("medium", "medium"),
                    ("high", "high"),
                ],
                current=vraw if veff else "none",
                enabled=veff,
            )
        )
        self._add_form_label(vadv, "label.reasoning", vre_w)

        self.vlm_sampling_hint = QLabel()
        self.vlm_sampling_hint.setObjectName("hint")
        self.vlm_sampling_hint.setWordWrap(True)
        vadv.addRow(self.vlm_sampling_hint)

        form.addRow(self.vlm_adv_group)
        outer.addWidget(self.vlm_panel)
        self._sync_vlm_api_meta_from_provider(apply_defaults=False)
        return self.recognize_group

    def _build_overlay_group(self, cfg: AppConfig) -> QGroupBox:
        """Present: Overlay appearance."""
        self.overlay_group = QGroupBox()
        # Legacy alias
        self.display_group = self.overlay_group
        form = QFormLayout(self.overlay_group)
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ov_hint = QLabel()
        self.ov_hint.setObjectName("hint")
        self.ov_hint.setWordWrap(True)
        form.addRow(self.ov_hint)

        # Content visibility (keep at least one checked)
        self.ov_show_source_check = QCheckBox()
        self.ov_show_source_check.setChecked(
            bool(getattr(cfg, "overlay_show_source", True))
        )
        self.ov_show_source_check.toggled.connect(self._on_ov_show_toggled)
        self.ov_show_translation_check = QCheckBox()
        self.ov_show_translation_check.setChecked(
            bool(getattr(cfg, "overlay_show_translation", True))
        )
        self.ov_show_translation_check.toggled.connect(self._on_ov_show_toggled)
        row_show = QHBoxLayout()
        row_show.addWidget(self.ov_show_source_check)
        row_show.addWidget(self.ov_show_translation_check)
        row_show.addStretch(1)
        show_wrap = QWidget()
        show_wrap.setLayout(row_show)
        form.addRow(show_wrap)

        self.opacity_spin = NoWheelDoubleSpinBox()
        self.opacity_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.opacity_spin.setRange(0.3, 1.0)
        self.opacity_spin.setSingleStep(0.05)
        self.opacity_spin.setValue(cfg.overlay_opacity)
        self.opacity_spin.valueChanged.connect(self._mark_dirty)

        self.ov_font_combo = NoWheelFontComboBox()
        self.ov_font_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ov_font_combo.setEditable(True)
        fam = str(getattr(cfg, "overlay_font_family", "") or "")
        if fam:
            self.ov_font_combo.setCurrentFont(QFont(fam))
        else:
            self.ov_font_combo.setCurrentIndex(-1)
            self.ov_font_combo.setEditText("")
        self.ov_font_combo.currentFontChanged.connect(self._mark_dirty)
        self.ov_font_combo.editTextChanged.connect(self._mark_dirty)

        self.ov_src_font_spin = NoWheelSpinBox()
        self.ov_src_font_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ov_src_font_spin.setRange(10, 48)
        self.ov_src_font_spin.setMinimumWidth(88)
        self.ov_src_font_spin.setValue(
            int(getattr(cfg, "overlay_source_font_size", 14) or 14)
        )
        self.ov_src_font_spin.valueChanged.connect(self._mark_dirty)
        self.ov_tr_font_spin = NoWheelSpinBox()
        self.ov_tr_font_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ov_tr_font_spin.setRange(10, 48)
        self.ov_tr_font_spin.setMinimumWidth(88)
        self.ov_tr_font_spin.setValue(
            int(
                getattr(
                    cfg,
                    "overlay_translation_font_size",
                    getattr(cfg, "overlay_font_size", 16),
                )
                or 16
            )
        )
        self.ov_tr_font_spin.valueChanged.connect(self._mark_dirty)

        self._expand_field(self.opacity_spin)
        self._add_form_label(form, "label.opacity", self.opacity_spin)

        self._expand_field(self.ov_font_combo)
        self._add_form_label(form, "label.font_family", self.ov_font_combo)

        row_sz = QHBoxLayout()
        self.lbl_ov_src_font = QLabel()
        self.lbl_ov_tr_font = QLabel()
        row_sz.addWidget(self.lbl_ov_src_font)
        row_sz.addWidget(self.ov_src_font_spin)
        row_sz.addWidget(self.lbl_ov_tr_font)
        row_sz.addWidget(self.ov_tr_font_spin)
        row_sz.addStretch(1)
        sz_wrap = QWidget()
        sz_wrap.setLayout(row_sz)
        self._add_form_label(form, "label.font_sizes", sz_wrap)

        self.ov_src_color_btn = ColorButton(
            getattr(cfg, "overlay_source_color", "#c8c8d8")
        )
        self.ov_src_color_btn.color_changed.connect(self._mark_dirty)
        self.ov_tr_color_btn = ColorButton(
            getattr(cfg, "overlay_translation_color", "#ffffff")
        )
        self.ov_tr_color_btn.color_changed.connect(self._mark_dirty)
        row_col = QHBoxLayout()
        self.lbl_ov_src_color = QLabel()
        self.lbl_ov_tr_color = QLabel()
        row_col.addWidget(self.lbl_ov_src_color)
        row_col.addWidget(self.ov_src_color_btn)
        row_col.addWidget(self.lbl_ov_tr_color)
        row_col.addWidget(self.ov_tr_color_btn)
        row_col.addStretch(1)
        col_wrap = QWidget()
        col_wrap.setLayout(row_col)
        self._add_form_label(form, "label.colors", col_wrap)

        self.ov_tr_bold_check = QCheckBox()
        self.ov_tr_bold_check.setChecked(
            bool(getattr(cfg, "overlay_translation_bold", True))
        )
        self.ov_tr_bold_check.toggled.connect(self._mark_dirty)

        self.ov_align_combo = NoWheelComboBox()
        self.ov_align_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ov_align_combo.addItem("", "left")
        self.ov_align_combo.addItem("", "center")
        self._set_combo(
            self.ov_align_combo, getattr(cfg, "overlay_text_align", "left") or "left"
        )
        self.ov_align_combo.currentIndexChanged.connect(self._mark_dirty)

        row_align = QHBoxLayout()
        row_align.addWidget(self.ov_tr_bold_check)
        row_align.addWidget(self.ov_align_combo)
        row_align.addStretch(1)
        align_wrap = QWidget()
        align_wrap.setLayout(row_align)
        form.addRow(align_wrap)

        self.ov_bg_color_btn = ColorButton(getattr(cfg, "overlay_bg_color", "#14141c"))
        self.ov_bg_color_btn.color_changed.connect(self._mark_dirty)
        self.ov_bg_alpha_spin = NoWheelSpinBox()
        self.ov_bg_alpha_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ov_bg_alpha_spin.setRange(0, 255)
        self.ov_bg_alpha_spin.setValue(_cfg_int(cfg, "overlay_bg_alpha", 210))
        self.ov_bg_alpha_spin.valueChanged.connect(self._mark_dirty)
        row_bg = QHBoxLayout()
        self.lbl_ov_bg = QLabel()
        self.lbl_ov_bg_alpha = QLabel()
        row_bg.addWidget(self.lbl_ov_bg)
        row_bg.addWidget(self.ov_bg_color_btn)
        row_bg.addWidget(self.lbl_ov_bg_alpha)
        row_bg.addWidget(self.ov_bg_alpha_spin)
        row_bg.addStretch(1)
        bg_wrap = QWidget()
        bg_wrap.setLayout(row_bg)
        self._add_form_label(form, "label.panel_bg", bg_wrap)

        self.click_through_check = QCheckBox()
        self.click_through_check.setChecked(cfg.overlay_click_through)
        self.click_through_check.toggled.connect(self._mark_dirty)
        form.addRow("", self.click_through_check)

        self.btn_overlay = QPushButton()
        self.btn_overlay.setObjectName("secondary")
        self.btn_overlay.clicked.connect(self.toggle_overlay_clicked.emit)
        form.addRow(self.btn_overlay)
        return self.overlay_group

    def _build_app_group(self, cfg: AppConfig) -> QGroupBox:
        """App-level settings: UI language, hotkey."""
        self.app_group = QGroupBox()
        form = QFormLayout(self.app_group)
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.ui_lang_combo = NoWheelComboBox()
        self.ui_lang_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for code, name in available_languages():
            self.ui_lang_combo.addItem(name, code)
        self._set_combo(
            self.ui_lang_combo, getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant"
        )
        self.ui_lang_combo.currentIndexChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.ui_language", self.ui_lang_combo)

        self.hotkey_edit = QLineEdit(cfg.hotkey)
        self._expand_field(self.hotkey_edit)
        self.hotkey_edit.textChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.hotkey", self.hotkey_edit)
        return self.app_group

    def _build_obs_group(self, cfg: AppConfig) -> QGroupBox:
        self.obs_group = QGroupBox()
        form = QFormLayout(self.obs_group)
        form.setSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.obs_hint = QLabel()
        self.obs_hint.setObjectName("hint")
        self.obs_hint.setWordWrap(True)
        form.addRow(self.obs_hint)

        self.obs_enabled_check = QCheckBox()
        self.obs_enabled_check.setChecked(bool(getattr(cfg, "obs_enabled", False)))
        self.obs_enabled_check.toggled.connect(self._mark_dirty)
        form.addRow(self.obs_enabled_check)

        self.obs_port_spin = NoWheelSpinBox()
        self.obs_port_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_port_spin.setRange(1, 65535)
        self.obs_port_spin.setValue(int(getattr(cfg, "obs_port", 8765) or 8765))
        self.obs_port_spin.valueChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.obs_port", self.obs_port_spin)

        self.obs_show_source_check = QCheckBox()
        self.obs_show_source_check.setChecked(
            bool(getattr(cfg, "obs_show_source", False))
        )
        self.obs_show_source_check.toggled.connect(self._on_obs_show_toggled)
        self.obs_show_translation_check = QCheckBox()
        self.obs_show_translation_check.setChecked(
            bool(getattr(cfg, "obs_show_translation", True))
        )
        self.obs_show_translation_check.toggled.connect(self._on_obs_show_toggled)
        row_obs_show = QHBoxLayout()
        row_obs_show.addWidget(self.obs_show_source_check)
        row_obs_show.addWidget(self.obs_show_translation_check)
        row_obs_show.addStretch(1)
        obs_show_wrap = QWidget()
        obs_show_wrap.setLayout(row_obs_show)
        form.addRow(obs_show_wrap)

        self.obs_font_combo = NoWheelFontComboBox()
        self.obs_font_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_font_combo.setEditable(True)
        ofam = str(getattr(cfg, "obs_font_family", "") or "")
        if ofam:
            self.obs_font_combo.setCurrentFont(QFont(ofam))
        else:
            self.obs_font_combo.setCurrentIndex(-1)
            self.obs_font_combo.setEditText("")
        self.obs_font_combo.currentFontChanged.connect(self._mark_dirty)
        self.obs_font_combo.editTextChanged.connect(self._mark_dirty)
        self._add_form_label(form, "label.font_family", self.obs_font_combo)

        self.obs_src_font_spin = NoWheelSpinBox()
        self.obs_src_font_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_src_font_spin.setRange(10, 96)
        self.obs_src_font_spin.setMinimumWidth(88)
        self.obs_src_font_spin.setValue(
            int(getattr(cfg, "obs_source_font_size", 20) or 20)
        )
        self.obs_src_font_spin.valueChanged.connect(self._mark_dirty)
        self.obs_tr_font_spin = NoWheelSpinBox()
        self.obs_tr_font_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_tr_font_spin.setRange(10, 96)
        self.obs_tr_font_spin.setMinimumWidth(88)
        self.obs_tr_font_spin.setValue(
            int(
                getattr(
                    cfg,
                    "obs_translation_font_size",
                    getattr(cfg, "obs_font_size", 28),
                )
                or 28
            )
        )
        self.obs_tr_font_spin.valueChanged.connect(self._mark_dirty)
        row_obs_sz = QHBoxLayout()
        self.lbl_obs_src_font = QLabel()
        self.lbl_obs_tr_font = QLabel()
        row_obs_sz.addWidget(self.lbl_obs_src_font)
        row_obs_sz.addWidget(self.obs_src_font_spin)
        row_obs_sz.addWidget(self.lbl_obs_tr_font)
        row_obs_sz.addWidget(self.obs_tr_font_spin)
        row_obs_sz.addStretch(1)
        obs_sz_wrap = QWidget()
        obs_sz_wrap.setLayout(row_obs_sz)
        self._add_form_label(form, "label.font_sizes", obs_sz_wrap)

        self.obs_src_color_btn = ColorButton(
            getattr(cfg, "obs_source_color", "#d8d8e0")
        )
        self.obs_src_color_btn.color_changed.connect(self._mark_dirty)
        self.obs_tr_color_btn = ColorButton(
            getattr(cfg, "obs_translation_color", "#ffffff")
        )
        self.obs_tr_color_btn.color_changed.connect(self._mark_dirty)
        row_obs_col = QHBoxLayout()
        self.lbl_obs_src_color = QLabel()
        self.lbl_obs_tr_color = QLabel()
        row_obs_col.addWidget(self.lbl_obs_src_color)
        row_obs_col.addWidget(self.obs_src_color_btn)
        row_obs_col.addWidget(self.lbl_obs_tr_color)
        row_obs_col.addWidget(self.obs_tr_color_btn)
        row_obs_col.addStretch(1)
        obs_col_wrap = QWidget()
        obs_col_wrap.setLayout(row_obs_col)
        self._add_form_label(form, "label.colors", obs_col_wrap)

        self.obs_tr_bold_check = QCheckBox()
        self.obs_tr_bold_check.setChecked(
            bool(getattr(cfg, "obs_translation_bold", True))
        )
        self.obs_tr_bold_check.toggled.connect(self._mark_dirty)
        self.obs_align_combo = NoWheelComboBox()
        self.obs_align_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_align_combo.addItem("", "left")
        self.obs_align_combo.addItem("", "center")
        self._set_combo(
            self.obs_align_combo, getattr(cfg, "obs_text_align", "left") or "left"
        )
        self.obs_align_combo.currentIndexChanged.connect(self._mark_dirty)
        row_obs_align = QHBoxLayout()
        row_obs_align.addWidget(self.obs_tr_bold_check)
        row_obs_align.addWidget(self.obs_align_combo)
        row_obs_align.addStretch(1)
        obs_align_wrap = QWidget()
        obs_align_wrap.setLayout(row_obs_align)
        form.addRow(obs_align_wrap)

        self.obs_bg_color_btn = ColorButton(getattr(cfg, "obs_bg_color", "#000000"))
        self.obs_bg_color_btn.color_changed.connect(self._mark_dirty)
        self.obs_bg_alpha_spin = NoWheelSpinBox()
        self.obs_bg_alpha_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.obs_bg_alpha_spin.setRange(0, 255)
        self.obs_bg_alpha_spin.setValue(_cfg_int(cfg, "obs_bg_alpha", 140))
        self.obs_bg_alpha_spin.valueChanged.connect(self._mark_dirty)
        row_obs_bg = QHBoxLayout()
        self.lbl_obs_bg = QLabel()
        self.lbl_obs_bg_alpha = QLabel()
        row_obs_bg.addWidget(self.lbl_obs_bg)
        row_obs_bg.addWidget(self.obs_bg_color_btn)
        row_obs_bg.addWidget(self.lbl_obs_bg_alpha)
        row_obs_bg.addWidget(self.obs_bg_alpha_spin)
        row_obs_bg.addStretch(1)
        obs_bg_wrap = QWidget()
        obs_bg_wrap.setLayout(row_obs_bg)
        self._add_form_label(form, "label.panel_bg", obs_bg_wrap)

        url_row = QHBoxLayout()
        self.obs_url_label = QLabel(f"http://127.0.0.1:{self.obs_port_spin.value()}/")
        self.obs_url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.btn_copy_obs = QPushButton()
        self.btn_copy_obs.setObjectName("secondary")
        self.btn_copy_obs.clicked.connect(self.copy_obs_url_clicked.emit)
        url_row.addWidget(self.obs_url_label, 1)
        url_row.addWidget(self.btn_copy_obs)
        url_wrap = QWidget()
        url_wrap.setLayout(url_row)
        self._add_form_label(form, "label.obs_url", url_wrap)

        self.obs_status_label = QLabel()
        self.obs_status_label.setObjectName("hint")
        self.obs_status_label.setWordWrap(True)
        form.addRow(self.obs_status_label)

        self.obs_port_spin.valueChanged.connect(self._update_obs_url_label)
        return self.obs_group

    def _update_obs_url_label(self, *_args) -> None:
        port = int(self.obs_port_spin.value())
        applied = int(getattr(self._cfg, "obs_port", port) or port)
        url = f"http://127.0.0.1:{port}/"
        if port != applied:
            self.obs_url_label.setText(tr("obs.url_draft", url=url))
        else:
            self.obs_url_label.setText(url)

    def _build_result_group(self) -> QGroupBox:
        from app.config import clamp_result_history_lines

        self.result_group = QGroupBox()
        res_l = QVBoxLayout(self.result_group)
        res_l.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.lbl_result_history = QLabel()
        self.result_history_spin = NoWheelSpinBox()
        self.result_history_spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.result_history_spin.setRange(1, 100)
        self.result_history_spin.setSingleStep(1)
        self.result_history_spin.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.result_history_spin.setFixedWidth(72)
        self.result_history_spin.setValue(
            clamp_result_history_lines(getattr(self._cfg, "result_history_lines", 12))
        )
        self.result_history_spin.valueChanged.connect(self._on_result_history_changed)
        head.addWidget(self.lbl_result_history)
        head.addWidget(self.result_history_spin)
        head.addStretch(1)
        self.btn_cache = QPushButton()
        self.btn_cache.setObjectName("secondary")
        self.btn_cache.clicked.connect(self.clear_cache_clicked.emit)
        head.addWidget(self.btn_cache)
        res_l.addLayout(head)

        self.result_view = QTextEdit()
        self.result_view.setReadOnly(True)
        self.result_view.setObjectName("resultLog")
        self.result_view.setMinimumHeight(88)
        self.result_view.setMaximumHeight(180)
        self.result_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        res_l.addWidget(self.result_view)
        return self.result_group

    def _build_footer(self) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch(1)
        self.btn_exit = QPushButton()
        self.btn_exit.setObjectName("danger")
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

    # ------------------------------------------------------------------ i18n

    def retranslate(self) -> None:
        """Refresh all user-visible strings for the current UI language."""
        dirty = self._dirty
        self.setWindowTitle(tr("app.title_dirty") if dirty else tr("app.title"))
        self.btn_start_monitor.setText(tr("btn.start_monitor"))
        self.btn_start_monitor.setToolTip(tr("tip.start_monitor"))
        self.btn_stop_monitor.setText(tr("btn.stop_monitor"))
        self.btn_stop_monitor.setToolTip(tr("tip.stop_monitor"))
        self.btn_apply.setText(tr("btn.apply"))
        self.btn_apply.setToolTip(tr("tip.apply"))
        self.btn_save.setText(tr("btn.save"))
        self.btn_save.setToolTip(tr("tip.save"))
        self.btn_cancel.setText(tr("btn.cancel"))
        self.btn_cancel.setToolTip(tr("tip.cancel"))
        self.settings_hint.setText(
            tr("hint.dirty") if dirty else tr("hint.apply_or_save")
        )

        if hasattr(self, "detect_group"):
            self.detect_group.setTitle(tr("group.detect"))
        if hasattr(self, "capture_group"):
            self.capture_group.setTitle(tr("group.capture"))
        if hasattr(self, "process_group"):
            self.process_group.setTitle(tr("group.process"))
        # recognize_group title is mode-dependent (set in _on_pipeline_mode_changed)
        self.llm_group.setTitle(tr("group.llm_translate"))
        if hasattr(self, "overlay_group"):
            self.overlay_group.setTitle(tr("group.present_overlay"))
        elif hasattr(self, "display_group"):
            self.display_group.setTitle(tr("group.present_overlay"))
        self.obs_group.setTitle(tr("group.present_obs"))
        self.result_group.setTitle(tr("group.present_result"))
        if hasattr(self, "app_group"):
            self.app_group.setTitle(tr("group.app"))
        if hasattr(self, "lbl_result_history"):
            self.lbl_result_history.setText(tr("label.result_history"))
        if hasattr(self, "result_history_spin"):
            self.result_history_spin.setToolTip(tr("tip.result_history"))
        self.adv_group.setTitle(tr("group.llm_advanced"))
        if hasattr(self, "vlm_adv_group"):
            self.vlm_adv_group.setTitle(tr("group.llm_advanced"))

        self.btn_refresh.setText(tr("btn.refresh"))
        self.btn_region.setText(tr("btn.region"))
        self.btn_translate.setText(tr("btn.translate"))
        self.btn_exit.setText(tr("btn.exit"))
        self.btn_exit.setToolTip(tr("tip.exit"))
        self.btn_overlay.setToolTip(tr("tip.overlay"))
        self.btn_cache.setText(tr("btn.clear_cache"))
        self.btn_cache.setToolTip(tr("tip.clear_cache"))
        self.btn_copy_obs.setText(tr("btn.copy_url"))
        self.btn_refresh_models.setText(tr("btn.refresh_models"))
        self.btn_refresh_models.setToolTip(tr("tip.refresh_models"))
        if hasattr(self, "btn_vlm_refresh_models"):
            self.btn_vlm_refresh_models.setText(tr("btn.refresh_models"))
            self.btn_vlm_refresh_models.setToolTip(tr("tip.refresh_models"))

        self.pipeline_mode_combo.setItemText(0, tr("mode.ocr"))
        self.pipeline_mode_combo.setItemText(1, tr("mode.vlm"))
        if self.pipeline_mode_combo.count() > 2:
            self.pipeline_mode_combo.setItemText(2, tr("mode.vlm_ocr"))
        self.pipeline_mode_combo.setToolTip(tr("tip.pipeline_mode"))
        self.lbl_preview.setText(tr("label.preview"))
        if not self.preview_label.pixmap() or self.preview_label.pixmap().isNull():
            self.preview_label.setText(tr("hint.preview_empty"))

        self.lbl_source.setText(tr("label.source"))
        self.lbl_target.setText(tr("label.target"))
        self.prompt_edit.setPlaceholderText(tr("hint.prompt"))
        self.api_key_edit.setPlaceholderText(tr("hint.api_key"))
        if hasattr(self, "vlm_api_key_edit"):
            self.vlm_api_key_edit.setPlaceholderText(tr("hint.vlm_api_key"))
        if hasattr(self, "vlm_prompt_edit"):
            self.vlm_prompt_edit.setPlaceholderText(tr("hint.vlm_prompt"))
        self.context_spin.setToolTip(tr("tip.context"))
        if hasattr(self, "timeout_spin"):
            self.timeout_spin.setToolTip(tr("tip.llm_timeout"))
        if hasattr(self, "vlm_timeout_spin"):
            self.vlm_timeout_spin.setToolTip(tr("tip.llm_timeout"))
        if hasattr(self, "sampling_hint"):
            self.sampling_hint.setText(tr("tip.sampling"))
        if hasattr(self, "vlm_sampling_hint"):
            self.vlm_sampling_hint.setText(tr("tip.sampling"))
        # Mode-dependent hints (recognize / translate)
        self._on_pipeline_mode_changed()

        self.ov_hint.setText(tr("hint.overlay"))
        self.ov_show_source_check.setText(tr("label.show_source"))
        self.ov_show_translation_check.setText(tr("label.show_translation"))
        self.lbl_ov_src_font.setText(tr("label.source_font"))
        self.lbl_ov_tr_font.setText(tr("label.translation_font"))
        self.lbl_ov_src_color.setText(tr("label.source_color"))
        self.lbl_ov_tr_color.setText(tr("label.translation_color"))
        self.ov_tr_bold_check.setText(tr("label.translation_bold"))
        self.lbl_ov_bg.setText(tr("label.bg_color"))
        self.lbl_ov_bg_alpha.setText(tr("label.bg_alpha"))
        self.ov_align_combo.setItemText(0, tr("align.left"))
        self.ov_align_combo.setItemText(1, tr("align.center"))
        self.click_through_check.setText(tr("check.click_through"))
        self.click_through_check.setToolTip(tr("tip.click_through"))
        self.ocr_combo.setToolTip(tr("tip.ocr"))
        for i, code in enumerate(("oneocr", "manga", "rapid", "paddle")):
            self.ocr_combo.setItemText(i, tr(f"ocr.{code}"))
        for i, code in enumerate(("auto", "wgc", "bitblt")):
            self.capture_method_combo.setItemText(i, tr(f"capture.{code}"))
        self.capture_method_combo.setToolTip(tr("tip.capture_method"))
        self.monitor_hint.setText(tr("hint.monitor_fields"))

        self.obs_hint.setText(tr("hint.obs"))
        self.obs_enabled_check.setText(tr("label.obs_enabled"))
        self.obs_show_source_check.setText(tr("label.show_source"))
        self.obs_show_translation_check.setText(tr("label.show_translation"))
        self.lbl_obs_src_font.setText(tr("label.source_font"))
        self.lbl_obs_tr_font.setText(tr("label.translation_font"))
        self.lbl_obs_src_color.setText(tr("label.source_color"))
        self.lbl_obs_tr_color.setText(tr("label.translation_color"))
        self.obs_tr_bold_check.setText(tr("label.translation_bold"))
        self.lbl_obs_bg.setText(tr("label.bg_color"))
        self.lbl_obs_bg_alpha.setText(tr("label.bg_alpha"))
        self.obs_align_combo.setItemText(0, tr("align.left"))
        self.obs_align_combo.setItemText(1, tr("align.center"))

        self.stable_ms_spin.setToolTip(tr("tip.stable_ms"))
        self.interval_spin.setToolTip(tr("tip.interval"))
        self.cooldown_spin.setToolTip(tr("tip.cooldown"))
        self.buffer_spin.setToolTip(tr("tip.pipeline_buffer"))
        self.threshold_spin.setToolTip(tr("tip.threshold"))

        for key, labs in self._form_labels.items():
            text = tr(key)
            for lab in labs:
                lab.setText(text)

        # reasoning combo labels (omit handled by checkbox, same as other sampling)
        mapping = {
            "none": "reasoning.none",
            "low": "reasoning.low",
            "medium": "reasoning.medium",
            "high": "reasoning.high",
        }
        for i in range(self.reasoning_combo.count()):
            code = self.reasoning_combo.itemData(i)
            self.reasoning_combo.setItemText(
                i, tr(mapping.get(code or "", "reasoning.none"))
            )

        self.set_overlay_button_state(self._overlay_visible)
        self._refresh_obs_status_label()
        self._update_region_label()
        self._update_obs_url_label()
        # keep window none item if present
        if self.window_combo.count() > 0 and self.window_combo.itemData(0) == 0:
            self.window_combo.setItemText(0, tr("window.none"))

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
                f"hwnd={c.bound_hwnd}" if c.bound_hwnd else tr("hint.region_screen")
            )
            self.region_label.setText(
                f"({c.region_x},{c.region_y}) {c.region_w}×{c.region_h} · {bound}"
            )
        else:
            self.region_label.setText(tr("hint.region_none"))

    def set_config(self, cfg: AppConfig) -> None:
        """Replace applied snapshot and reload UI (e.g. Cancel / external update)."""
        self._cfg = deepcopy(cfg)
        set_language(getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant")
        self.load_from_config(cfg)
        self.retranslate()
        self._update_region_label()

    def load_from_config(self, cfg: AppConfig) -> None:
        """Fill form fields from cfg without marking dirty."""
        self._loading_ui = True
        try:
            self._set_combo(self.provider_combo, cfg.api_provider)
            self._sync_api_meta_from_provider(apply_defaults=False)
            self.api_key_edit.setText(cfg.api_key or "")
            self.base_url_edit.setText(cfg.base_url or "")
            self.set_model_text(cfg.model or "")
            self.prompt_edit.setText(cfg.custom_prompt or "")
            self.context_spin.setValue(
                int(getattr(cfg, "context_history_size", 3) or 0)
            )
            self.max_tokens_spin.setValue(int(getattr(cfg, "max_tokens", 2048) or 2048))
            if hasattr(self, "timeout_spin"):
                self.timeout_spin.setValue(
                    float(getattr(cfg, "llm_timeout_s", 30) or 30)
                )
            # VLM endpoint
            if hasattr(self, "vlm_provider_combo"):
                self._set_combo(
                    self.vlm_provider_combo,
                    getattr(cfg, "vlm_api_provider", "xai") or "xai",
                )
                self._sync_vlm_api_meta_from_provider(apply_defaults=False)
                self.vlm_api_key_edit.setText(getattr(cfg, "vlm_api_key", "") or "")
                self.vlm_base_url_edit.setText(getattr(cfg, "vlm_base_url", "") or "")
                self.set_model_text(getattr(cfg, "vlm_model", "") or "", role="vlm")
                self.vlm_prompt_edit.setText(
                    getattr(cfg, "vlm_custom_prompt", "") or ""
                )
                self.vlm_max_tokens_spin.setValue(
                    int(getattr(cfg, "vlm_max_tokens", 2048) or 2048)
                )
                self.vlm_timeout_spin.setValue(
                    float(getattr(cfg, "vlm_timeout_s", 30) or 30)
                )
            self._set_combo(self.source_combo, cfg.source_lang)
            self._set_combo(self.target_combo, cfg.target_lang)
            self.hotkey_edit.setText(cfg.hotkey or "Ctrl+Shift+T")
            self.opacity_spin.setValue(cfg.overlay_opacity)
            self.ov_show_source_check.setChecked(
                bool(getattr(cfg, "overlay_show_source", True))
            )
            self.ov_show_translation_check.setChecked(
                bool(getattr(cfg, "overlay_show_translation", True))
            )
            ofam = str(getattr(cfg, "overlay_font_family", "") or "")
            if ofam:
                self.ov_font_combo.setCurrentFont(QFont(ofam))
            else:
                self.ov_font_combo.setCurrentIndex(-1)
                self.ov_font_combo.setEditText("")
            self.ov_src_font_spin.setValue(
                int(getattr(cfg, "overlay_source_font_size", 14) or 14)
            )
            self.ov_tr_font_spin.setValue(
                int(
                    getattr(
                        cfg,
                        "overlay_translation_font_size",
                        getattr(cfg, "overlay_font_size", 16),
                    )
                    or 16
                )
            )
            self.ov_src_color_btn.set_color(
                getattr(cfg, "overlay_source_color", "#c8c8d8")
            )
            self.ov_tr_color_btn.set_color(
                getattr(cfg, "overlay_translation_color", "#ffffff")
            )
            self.ov_tr_bold_check.setChecked(
                bool(getattr(cfg, "overlay_translation_bold", True))
            )
            self._set_combo(
                self.ov_align_combo,
                getattr(cfg, "overlay_text_align", "left") or "left",
            )
            self.ov_bg_color_btn.set_color(getattr(cfg, "overlay_bg_color", "#14141c"))
            self.ov_bg_alpha_spin.setValue(_cfg_int(cfg, "overlay_bg_alpha", 210))
            self.click_through_check.setChecked(cfg.overlay_click_through)
            self._set_combo(
                self.ocr_combo,
                normalize_ocr_engine(cfg.ocr_engine or DEFAULT_OCR_ENGINE),
            )
            self._set_combo(
                self.pipeline_mode_combo,
                (getattr(cfg, "pipeline_mode", "ocr") or "ocr"),
            )
            self._set_combo(
                self.capture_method_combo,
                str(getattr(cfg, "window_capture_method", "auto") or "auto"),
            )
            self._set_combo(
                self.ui_lang_combo,
                getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant",
            )
            stable_ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
            if not getattr(cfg, "monitor_wait_stable", True):
                stable_ms = 0
            self.stable_ms_spin.setValue(max(0, stable_ms))
            self.interval_spin.setValue(int(cfg.monitor_interval_ms or 0))
            self.cooldown_spin.setValue(
                int(getattr(cfg, "monitor_cooldown_ms", 1200) or 0)
            )
            self.threshold_spin.setValue(float(cfg.monitor_diff_threshold))
            from app.config import clamp_pipeline_buffer_size

            self.buffer_spin.setValue(
                clamp_pipeline_buffer_size(getattr(cfg, "pipeline_buffer_size", 3))
            )
            from app.config import clamp_result_history_lines

            if hasattr(self, "result_history_spin"):
                self.result_history_spin.setValue(
                    clamp_result_history_lines(getattr(cfg, "result_history_lines", 12))
                )

            # optional sampling
            def _load_opt_float(chk, spin, val, default):
                if val is None:
                    chk.setChecked(False)
                    spin.setEnabled(False)
                    spin.setValue(default)
                else:
                    chk.setChecked(True)
                    spin.setEnabled(True)
                    spin.setValue(float(val))

            def _load_opt_int(chk, spin, val, default):
                # top_k: None or 0 = unset; other ints use None-only for unset
                topk_unset = spin is self.topk_spin and (
                    val is None or (isinstance(val, int) and int(val) <= 0)
                )
                if val is None or topk_unset:
                    chk.setChecked(False)
                    spin.setEnabled(False)
                    spin.setValue(default)
                else:
                    chk.setChecked(True)
                    spin.setEnabled(True)
                    spin.setValue(int(val))

            _load_opt_float(
                self.temp_chk, self.temp_spin, getattr(cfg, "temperature", None), 0.2
            )
            _load_opt_float(
                self.topp_chk, self.topp_spin, getattr(cfg, "top_p", None), 1.0
            )
            topk = getattr(cfg, "top_k", None)
            if topk is not None and int(topk) > 0:
                self.topk_chk.setChecked(True)
                self.topk_spin.setEnabled(True)
                self.topk_spin.setValue(int(topk))
            else:
                self.topk_chk.setChecked(False)
                self.topk_spin.setEnabled(False)
                self.topk_spin.setValue(40)
            _load_opt_float(
                self.fp_chk, self.fp_spin, getattr(cfg, "frequency_penalty", None), 0.0
            )
            _load_opt_float(
                self.pp_chk, self.pp_spin, getattr(cfg, "presence_penalty", None), 0.0
            )
            seed = getattr(cfg, "seed", None)
            if seed is not None:
                self.seed_chk.setChecked(True)
                self.seed_spin.setEnabled(True)
                self.seed_spin.setValue(int(seed))
            else:
                self.seed_chk.setChecked(False)
                self.seed_spin.setEnabled(False)
                self.seed_spin.setValue(0)
            effort = (getattr(cfg, "reasoning_effort", "") or "").strip().lower()
            if effort in ("none", "low", "medium", "high"):
                self.reasoning_chk.setChecked(True)
                self.reasoning_combo.setEnabled(True)
                self._set_combo(self.reasoning_combo, effort)
            else:
                self.reasoning_chk.setChecked(False)
                self.reasoning_combo.setEnabled(False)

            # VLM sampling (independent)
            if hasattr(self, "vlm_temp_chk"):
                _load_opt_float(
                    self.vlm_temp_chk,
                    self.vlm_temp_spin,
                    getattr(cfg, "vlm_temperature", None),
                    0.2,
                )
                _load_opt_float(
                    self.vlm_topp_chk,
                    self.vlm_topp_spin,
                    getattr(cfg, "vlm_top_p", None),
                    1.0,
                )
                vtopk = getattr(cfg, "vlm_top_k", None)
                if vtopk is not None and int(vtopk) > 0:
                    self.vlm_topk_chk.setChecked(True)
                    self.vlm_topk_spin.setEnabled(True)
                    self.vlm_topk_spin.setValue(int(vtopk))
                else:
                    self.vlm_topk_chk.setChecked(False)
                    self.vlm_topk_spin.setEnabled(False)
                    self.vlm_topk_spin.setValue(40)
                _load_opt_float(
                    self.vlm_fp_chk,
                    self.vlm_fp_spin,
                    getattr(cfg, "vlm_frequency_penalty", None),
                    0.0,
                )
                _load_opt_float(
                    self.vlm_pp_chk,
                    self.vlm_pp_spin,
                    getattr(cfg, "vlm_presence_penalty", None),
                    0.0,
                )
                vseed = getattr(cfg, "vlm_seed", None)
                if vseed is not None:
                    self.vlm_seed_chk.setChecked(True)
                    self.vlm_seed_spin.setEnabled(True)
                    self.vlm_seed_spin.setValue(int(vseed))
                else:
                    self.vlm_seed_chk.setChecked(False)
                    self.vlm_seed_spin.setEnabled(False)
                    self.vlm_seed_spin.setValue(0)
                veffort = (
                    (getattr(cfg, "vlm_reasoning_effort", "") or "").strip().lower()
                )
                if veffort in ("none", "low", "medium", "high"):
                    self.vlm_reasoning_chk.setChecked(True)
                    self.vlm_reasoning_combo.setEnabled(True)
                    self._set_combo(self.vlm_reasoning_combo, veffort)
                else:
                    self.vlm_reasoning_chk.setChecked(False)
                    self.vlm_reasoning_combo.setEnabled(False)

            self.obs_enabled_check.setChecked(bool(getattr(cfg, "obs_enabled", False)))
            self.obs_port_spin.setValue(int(getattr(cfg, "obs_port", 8765) or 8765))
            self.obs_show_source_check.setChecked(
                bool(getattr(cfg, "obs_show_source", False))
            )
            self.obs_show_translation_check.setChecked(
                bool(getattr(cfg, "obs_show_translation", True))
            )
            obs_fam = str(getattr(cfg, "obs_font_family", "") or "")
            if obs_fam:
                self.obs_font_combo.setCurrentFont(QFont(obs_fam))
            else:
                self.obs_font_combo.setCurrentIndex(-1)
                self.obs_font_combo.setEditText("")
            self.obs_src_font_spin.setValue(
                int(getattr(cfg, "obs_source_font_size", 20) or 20)
            )
            self.obs_tr_font_spin.setValue(
                int(
                    getattr(
                        cfg,
                        "obs_translation_font_size",
                        getattr(cfg, "obs_font_size", 28),
                    )
                    or 28
                )
            )
            self.obs_src_color_btn.set_color(
                getattr(cfg, "obs_source_color", "#d8d8e0")
            )
            self.obs_tr_color_btn.set_color(
                getattr(cfg, "obs_translation_color", "#ffffff")
            )
            self.obs_tr_bold_check.setChecked(
                bool(getattr(cfg, "obs_translation_bold", True))
            )
            self._set_combo(
                self.obs_align_combo, getattr(cfg, "obs_text_align", "left") or "left"
            )
            self.obs_bg_color_btn.set_color(getattr(cfg, "obs_bg_color", "#000000"))
            self.obs_bg_alpha_spin.setValue(_cfg_int(cfg, "obs_bg_alpha", 140))
            self._update_obs_url_label()
            self._on_pipeline_mode_changed()
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
        c.model = self.model_text().strip() or c.model
        c.custom_prompt = self.prompt_edit.text().strip()
        c.context_history_size = int(self.context_spin.value())
        c.max_tokens = int(self.max_tokens_spin.value())
        if hasattr(self, "timeout_spin"):
            c.llm_timeout_s = float(self.timeout_spin.value())
        # VLM endpoint (independent)
        if hasattr(self, "vlm_provider_combo"):
            vpid = self.vlm_provider_combo.currentData() or "xai"
            c.vlm_api_provider = vpid
            vpreset = get_preset(vpid)
            c.vlm_api_protocol = (
                vpreset.protocol if vpreset else (c.vlm_api_protocol or "openai")
            )
            c.vlm_api_key = self.vlm_api_key_edit.text().strip()
            c.vlm_base_url = self.vlm_base_url_edit.text().strip() or (
                vpreset.base_url if vpreset else "https://api.x.ai/v1"
            )
            c.vlm_model = self.model_text(role="vlm").strip() or c.vlm_model
            c.vlm_custom_prompt = self.vlm_prompt_edit.text().strip()
            c.vlm_max_tokens = int(self.vlm_max_tokens_spin.value())
            c.vlm_timeout_s = float(self.vlm_timeout_spin.value())
            c.vlm_temperature = _optional_float_row(
                enabled=self.vlm_temp_chk.isChecked(), spin=self.vlm_temp_spin
            )
            c.vlm_top_p = _optional_float_row(
                enabled=self.vlm_topp_chk.isChecked(), spin=self.vlm_topp_spin
            )
            c.vlm_top_k = (
                int(self.vlm_topk_spin.value())
                if self.vlm_topk_chk.isChecked() and int(self.vlm_topk_spin.value()) > 0
                else None
            )
            c.vlm_frequency_penalty = _optional_float_row(
                enabled=self.vlm_fp_chk.isChecked(), spin=self.vlm_fp_spin
            )
            c.vlm_presence_penalty = _optional_float_row(
                enabled=self.vlm_pp_chk.isChecked(), spin=self.vlm_pp_spin
            )
            c.vlm_seed = _optional_int_row(
                enabled=self.vlm_seed_chk.isChecked(), spin=self.vlm_seed_spin
            )
            if self.vlm_reasoning_chk.isChecked():
                c.vlm_reasoning_effort = str(
                    self.vlm_reasoning_combo.currentData() or "none"
                )
            else:
                c.vlm_reasoning_effort = ""
        c.source_lang = self.source_combo.currentData() or "ja"
        c.target_lang = self.target_combo.currentData() or "zh-Hant"
        c.hotkey = self.hotkey_edit.text().strip() or "Ctrl+Shift+T"
        c.overlay_opacity = float(self.opacity_spin.value())
        c.overlay_show_source = self.ov_show_source_check.isChecked()
        c.overlay_show_translation = self.ov_show_translation_check.isChecked()
        if not c.overlay_show_source and not c.overlay_show_translation:
            c.overlay_show_translation = True
        c.overlay_font_family = self.ov_font_combo.currentText().strip()
        c.overlay_source_font_size = int(self.ov_src_font_spin.value())
        c.overlay_translation_font_size = int(self.ov_tr_font_spin.value())
        c.overlay_font_size = c.overlay_translation_font_size
        c.overlay_source_color = normalize_hex_color(
            self.ov_src_color_btn.color(), "#c8c8d8"
        )
        c.overlay_translation_color = normalize_hex_color(
            self.ov_tr_color_btn.color(), "#ffffff"
        )
        c.overlay_translation_bold = self.ov_tr_bold_check.isChecked()
        c.overlay_text_align = self.ov_align_combo.currentData() or "left"
        c.overlay_bg_color = normalize_hex_color(
            self.ov_bg_color_btn.color(), "#14141c"
        )
        c.overlay_bg_alpha = int(self.ov_bg_alpha_spin.value())
        c.overlay_click_through = self.click_through_check.isChecked()
        c.ocr_engine = normalize_ocr_engine(
            self.ocr_combo.currentData() or DEFAULT_OCR_ENGINE
        )
        c.pipeline_mode = self.pipeline_mode_combo.currentData() or "ocr"
        c.window_capture_method = self.capture_method_combo.currentData() or "auto"
        c.ui_language = self.ui_lang_combo.currentData() or "zh-Hant"
        c.auto_monitor = bool(self._monitor_running)
        c.monitor_stable_ms = int(self.stable_ms_spin.value())
        c.monitor_wait_stable = c.monitor_stable_ms > 0
        c.monitor_interval_ms = int(self.interval_spin.value())
        c.monitor_cooldown_ms = int(self.cooldown_spin.value())
        c.monitor_diff_threshold = float(self.threshold_spin.value())
        from app.config import clamp_pipeline_buffer_size

        c.pipeline_buffer_size = clamp_pipeline_buffer_size(self.buffer_spin.value())
        from app.config import clamp_result_history_lines

        c.result_history_lines = clamp_result_history_lines(
            self.result_history_spin.value()
            if hasattr(self, "result_history_spin")
            else getattr(c, "result_history_lines", 12)
        )

        c.temperature = _optional_float_row(
            enabled=self.temp_chk.isChecked(), spin=self.temp_spin
        )
        c.top_p = _optional_float_row(
            enabled=self.topp_chk.isChecked(), spin=self.topp_spin
        )
        c.top_k = (
            int(self.topk_spin.value())
            if self.topk_chk.isChecked() and int(self.topk_spin.value()) > 0
            else None
        )
        c.frequency_penalty = _optional_float_row(
            enabled=self.fp_chk.isChecked(), spin=self.fp_spin
        )
        c.presence_penalty = _optional_float_row(
            enabled=self.pp_chk.isChecked(), spin=self.pp_spin
        )
        c.seed = _optional_int_row(
            enabled=self.seed_chk.isChecked(), spin=self.seed_spin
        )
        if self.reasoning_chk.isChecked():
            c.reasoning_effort = str(self.reasoning_combo.currentData() or "none")
        else:
            c.reasoning_effort = ""

        c.obs_enabled = self.obs_enabled_check.isChecked()
        c.obs_port = int(self.obs_port_spin.value())
        c.obs_show_source = self.obs_show_source_check.isChecked()
        c.obs_show_translation = self.obs_show_translation_check.isChecked()
        if not c.obs_show_source and not c.obs_show_translation:
            c.obs_show_translation = True
        c.obs_font_family = self.obs_font_combo.currentText().strip()
        c.obs_source_font_size = int(self.obs_src_font_spin.value())
        c.obs_translation_font_size = int(self.obs_tr_font_spin.value())
        c.obs_font_size = c.obs_translation_font_size
        c.obs_source_color = normalize_hex_color(
            self.obs_src_color_btn.color(), "#d8d8e0"
        )
        c.obs_translation_color = normalize_hex_color(
            self.obs_tr_color_btn.color(), "#ffffff"
        )
        c.obs_translation_bold = self.obs_tr_bold_check.isChecked()
        c.obs_text_align = self.obs_align_combo.currentData() or "left"
        c.obs_bg_color = normalize_hex_color(self.obs_bg_color_btn.color(), "#000000")
        c.obs_bg_alpha = int(self.obs_bg_alpha_spin.value())

        geo = self.geometry()
        c.main_window_x, c.main_window_y = geo.x(), geo.y()
        c.main_window_w, c.main_window_h = geo.width(), geo.height()
        return c

    def mark_applied(self, cfg: AppConfig) -> None:
        """Called after successful Apply/Save."""
        self._cfg = deepcopy(cfg)
        lang = getattr(cfg, "ui_language", "zh-Hant") or "zh-Hant"
        set_language(lang)
        self.retranslate()
        self._set_dirty(False)
        self._update_region_label()

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if dirty:
            self.settings_hint.setText(tr("hint.dirty"))
            self.setWindowTitle(tr("app.title_dirty"))
        else:
            self.settings_hint.setText(tr("hint.apply_or_save"))
            self.setWindowTitle(tr("app.title"))

    def _mark_dirty(self, *_args) -> None:
        if self._loading_ui or self._applying_preset:
            return
        self._set_dirty(True)

    def _on_pipeline_mode_changed(self, *_args) -> None:
        """Show only settings used by the selected pipeline mode."""
        mode = self.pipeline_mode_combo.currentData() or "ocr"
        is_ocr = mode == "ocr"
        is_vlm = mode == "vlm"
        is_vlm_ocr = mode == "vlm_ocr"
        use_vlm = is_vlm or is_vlm_ocr
        use_translate = is_ocr or is_vlm_ocr

        if hasattr(self, "local_ocr_panel"):
            self.local_ocr_panel.setVisible(is_ocr)
        if hasattr(self, "vlm_panel"):
            self.vlm_panel.setVisible(use_vlm)
        if hasattr(self, "llm_group"):
            self.llm_group.setVisible(use_translate)

        self.ocr_combo.setEnabled(is_ocr)

        # Dynamic titles + hints
        if hasattr(self, "recognize_group"):
            if is_ocr:
                self.recognize_group.setTitle(tr("group.recognize_local"))
            elif is_vlm:
                self.recognize_group.setTitle(tr("group.recognize_vlm_direct"))
            else:
                self.recognize_group.setTitle(tr("group.recognize_vlm"))

        if hasattr(self, "recognize_hint"):
            if is_ocr:
                self.recognize_hint.setText(tr("hint.recognize_local_active"))
            elif is_vlm:
                self.recognize_hint.setText(tr("hint.recognize_vlm_direct"))
            else:
                self.recognize_hint.setText(tr("hint.recognize_vlm_active"))

        if hasattr(self, "vlm_optional_hint") and use_vlm:
            if is_vlm:
                self.vlm_optional_hint.setText(tr("hint.vlm_direct"))
            else:
                self.vlm_optional_hint.setText(tr("hint.vlm_optional"))

        if hasattr(self, "llm_optional_hint") and use_translate:
            self.llm_optional_hint.setText(tr("hint.llm_optional"))

    def _on_provider_changed(self, _idx: int = 0) -> None:
        if self._loading_ui or self._applying_preset:
            return
        self._sync_api_meta_from_provider(apply_defaults=True)
        self._mark_dirty()

    def _on_vlm_provider_changed(self, _idx: int = 0) -> None:
        if self._loading_ui or self._applying_preset:
            return
        self._sync_vlm_api_meta_from_provider(apply_defaults=True)
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

        if preset.env_keys and not self.api_key_edit.placeholderText():
            self.api_key_edit.setPlaceholderText(" / ".join(preset.env_keys))

        if apply_defaults:
            self._applying_preset = True
            try:
                self.base_url_edit.setText(preset.base_url)
                if preset.model:
                    self.set_model_text(preset.model)
            finally:
                self._applying_preset = False

    def _sync_vlm_api_meta_from_provider(self, *, apply_defaults: bool) -> None:
        if not hasattr(self, "vlm_provider_combo"):
            return
        pid = self.vlm_provider_combo.currentData() or "xai"
        preset = get_preset(pid)
        if not preset:
            self.vlm_api_meta_label.setText("")
            return

        if preset.protocol == "openai":
            style = "OpenAI Compatible · POST {base}/chat/completions"
        else:
            style = "Anthropic Compatible · POST {base}/v1/messages"

        bits = [style]
        if preset.hint:
            bits.append(preset.hint)
        self.vlm_api_meta_label.setText(" · ".join(bits))

        if apply_defaults:
            self._applying_preset = True
            try:
                self.vlm_base_url_edit.setText(preset.base_url)
                if preset.model:
                    self.set_model_text(preset.model, role="vlm")
            finally:
                self._applying_preset = False

    def model_text(self, role: str = "translate") -> str:
        combo = (
            self.vlm_model_combo
            if role == "vlm" and hasattr(self, "vlm_model_combo")
            else self.model_combo
        )
        if combo.lineEdit() is not None:
            return combo.lineEdit().text()
        return combo.currentText()

    def set_model_text(self, model: str, *, role: str = "translate") -> None:
        model = model or ""
        combo = (
            self.vlm_model_combo
            if role == "vlm" and hasattr(self, "vlm_model_combo")
            else self.model_combo
        )
        combo.blockSignals(True)
        try:
            idx = combo.findText(model, Qt.MatchFlag.MatchExactly)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif combo.lineEdit() is not None:
                combo.lineEdit().setText(model)
            else:
                combo.setCurrentText(model)
        finally:
            combo.blockSignals(False)

    def _on_model_combo_changed(self, _idx: int = 0) -> None:
        if self._loading_ui or self._applying_preset:
            return
        # Selecting from list writes into editable line
        if self.model_combo.lineEdit() is not None and self.model_combo.currentData():
            self.model_combo.lineEdit().setText(str(self.model_combo.currentData()))
        self._mark_dirty()

    def _on_vlm_model_combo_changed(self, _idx: int = 0) -> None:
        if self._loading_ui or self._applying_preset:
            return
        if (
            hasattr(self, "vlm_model_combo")
            and self.vlm_model_combo.lineEdit() is not None
            and self.vlm_model_combo.currentData()
        ):
            self.vlm_model_combo.lineEdit().setText(
                str(self.vlm_model_combo.currentData())
            )
        self._mark_dirty()

    def set_models_list(
        self, models: list[str], *, keep_current: bool = True, role: str = "translate"
    ) -> None:
        """Populate model dropdown; keep current typed model if present."""
        combo = (
            self.vlm_model_combo
            if role == "vlm" and hasattr(self, "vlm_model_combo")
            else self.model_combo
        )
        current = self.model_text(role=role).strip() if keep_current else ""
        ids = list(models or [])
        if role == "vlm":
            self._vlm_model_ids = ids
        else:
            self._model_ids = ids
        combo.blockSignals(True)
        try:
            combo.clear()
            for mid in ids:
                combo.addItem(mid, mid)
            if current:
                idx = combo.findText(current, Qt.MatchFlag.MatchExactly)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                elif combo.lineEdit() is not None:
                    combo.lineEdit().setText(current)
                else:
                    combo.setEditText(current)
            elif ids:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)

    def set_models_status(self, msg: str, *, role: str = "translate") -> None:
        if role == "vlm" and hasattr(self, "vlm_models_status_label"):
            self.vlm_models_status_label.setText(msg or "")
        else:
            self.models_status_label.setText(msg or "")

    def set_models_refreshing(self, busy: bool, *, role: str = "translate") -> None:
        if role == "vlm" and hasattr(self, "btn_vlm_refresh_models"):
            self.btn_vlm_refresh_models.setEnabled(not busy)
            if busy:
                self.vlm_models_status_label.setText(tr("models.loading"))
        else:
            self.btn_refresh_models.setEnabled(not busy)
            if busy:
                self.models_status_label.setText(tr("models.loading"))

    def set_windows(self, windows: list[WindowInfo]) -> None:
        self._windows = windows
        current_hwnd = self._cfg.bound_hwnd
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        self.window_combo.addItem(tr("window.none"), 0)
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
        """Legacy: operational actions now persist immediately; keep for call-site compat."""
        pass

    def sync_operational_monitor(self, cfg: AppConfig) -> None:
        """Update applied snapshot + form monitor fields without marking draft dirty."""
        self._cfg.auto_monitor = bool(cfg.auto_monitor)
        self._cfg.monitor_stable_ms = int(cfg.monitor_stable_ms)
        self._cfg.monitor_wait_stable = bool(cfg.monitor_wait_stable)
        self._cfg.monitor_interval_ms = int(cfg.monitor_interval_ms)
        self._cfg.monitor_cooldown_ms = int(cfg.monitor_cooldown_ms)
        self._cfg.monitor_diff_threshold = float(cfg.monitor_diff_threshold)
        from app.config import clamp_pipeline_buffer_size

        self._cfg.pipeline_buffer_size = clamp_pipeline_buffer_size(
            getattr(cfg, "pipeline_buffer_size", 3)
        )
        self._cfg.window_capture_method = str(
            getattr(cfg, "window_capture_method", "auto") or "auto"
        )
        self._loading_ui = True
        try:
            self.stable_ms_spin.setValue(max(0, int(cfg.monitor_stable_ms)))
            self.interval_spin.setValue(int(cfg.monitor_interval_ms or 0))
            self.cooldown_spin.setValue(int(cfg.monitor_cooldown_ms or 0))
            self.threshold_spin.setValue(float(cfg.monitor_diff_threshold))
            self.buffer_spin.setValue(self._cfg.pipeline_buffer_size)
            self._set_combo(
                self.capture_method_combo,
                str(getattr(cfg, "window_capture_method", "auto") or "auto"),
            )
        finally:
            self._loading_ui = False

    def _on_ov_show_toggled(self, *_args) -> None:
        if self._loading_ui:
            return
        # Keep at least one content surface enabled
        if (
            not self.ov_show_source_check.isChecked()
            and not self.ov_show_translation_check.isChecked()
        ):
            self.ov_show_translation_check.blockSignals(True)
            self.ov_show_translation_check.setChecked(True)
            self.ov_show_translation_check.blockSignals(False)
        self._mark_dirty()

    def _on_obs_show_toggled(self, *_args) -> None:
        if self._loading_ui:
            return
        if (
            not self.obs_show_source_check.isChecked()
            and not self.obs_show_translation_check.isChecked()
        ):
            self.obs_show_translation_check.blockSignals(True)
            self.obs_show_translation_check.setChecked(True)
            self.obs_show_translation_check.blockSignals(False)
        self._mark_dirty()

    def set_result_text(self, text: str) -> None:
        """Replace the entire result log (legacy / rare full overwrite)."""
        text = (text or "").strip()
        if not text:
            self._result_lines = []
            self.result_view.clear()
            return
        lines = [ln for ln in text.splitlines() if ln.strip()]
        max_n = self._result_history_cap()
        self._result_lines = lines[-max_n:]
        self._refresh_result_view()

    def append_result_line(self, line: str) -> None:
        """Append one concise result line and trim to configured history length."""
        line = (line or "").strip()
        if not line:
            return
        # Collapse internal newlines so each turn stays one log line
        line = " ".join(line.split())
        self._result_lines.append(line)
        max_n = self._result_history_cap()
        if len(self._result_lines) > max_n:
            self._result_lines = self._result_lines[-max_n:]
        self._refresh_result_view()

    def _result_history_cap(self) -> int:
        from app.config import clamp_result_history_lines

        try:
            return clamp_result_history_lines(self.result_history_spin.value())
        except Exception:
            return clamp_result_history_lines(
                getattr(self._cfg, "result_history_lines", 12)
            )

    def _on_result_history_changed(self, *_args) -> None:
        if self._loading_ui:
            return
        max_n = self._result_history_cap()
        if len(self._result_lines) > max_n:
            self._result_lines = self._result_lines[-max_n:]
            self._refresh_result_view()
        self._mark_dirty()

    def _refresh_result_view(self) -> None:
        self.result_view.setPlainText("\n".join(self._result_lines))
        # Keep newest visible
        cursor = self.result_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.result_view.setTextCursor(cursor)

    def set_preview_image(self, img: Image.Image | None) -> None:
        """Show capture preview on the main thread."""
        if img is None:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(tr("hint.preview_empty"))
            return
        rgb = img.convert("RGB")
        data = rgb.tobytes("raw", "RGB")
        qimg = QImage(
            data,
            rgb.width,
            rgb.height,
            rgb.width * 3,
            QImage.Format.Format_RGB888,
        )
        # Keep a copy so buffer lifetime is safe
        qimg = qimg.copy()
        pix = QPixmap.fromImage(qimg)
        target = self.preview_label.size()
        if target.width() < 10 or target.height() < 10:
            target = self.preview_label.minimumSize()
        scaled = pix.scaled(
            max(target.width(), 200),
            max(target.height(), 120),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    def set_status(self, msg: str, *, busy: bool | None = None) -> None:
        text = msg or ""
        self.statusBar().showMessage(text)
        if hasattr(self, "work_status") and self.work_status is not None:
            self.work_status.setText(text if text else tr("status.ready_short"))
            if busy is not None:
                self.work_status.setProperty("busy", "true" if busy else "false")
                self.work_status.style().unpolish(self.work_status)
                self.work_status.style().polish(self.work_status)

    def set_busy(self, busy: bool) -> None:
        # Always re-enable when idle so a failed pipeline cannot leave UI stuck.
        self._pipeline_busy = bool(busy)
        idle = not busy
        self.btn_translate.setEnabled(idle)
        self.btn_region.setEnabled(idle)
        # Start monitor only when idle and not already running.
        self.btn_start_monitor.setEnabled(idle and not self._monitor_running)
        # Stop monitor must work during OCR/translate — never gated by busy.
        self.btn_stop_monitor.setEnabled(self._monitor_running)
        self.btn_apply.setEnabled(idle)
        self.btn_save.setEnabled(idle)
        self.btn_cancel.setEnabled(idle)
        self.window_combo.setEnabled(idle)
        self.btn_refresh.setEnabled(idle)
        if busy and hasattr(self, "work_status"):
            cur = self.work_status.text() or tr("status.processing")
            self.set_status(cur, busy=True)
        elif not busy and hasattr(self, "work_status"):
            self.work_status.setProperty("busy", "false")
            self.work_status.style().unpolish(self.work_status)
            self.work_status.style().polish(self.work_status)
            self._set_monitor_buttons(self._monitor_running)

    def set_overlay_button_state(self, visible: bool) -> None:
        self._overlay_visible = bool(visible)
        if visible:
            self.btn_overlay.setText(tr("btn.overlay_hide"))
        else:
            self.btn_overlay.setText(tr("btn.overlay_show"))

    def set_obs_status(self, *, running: bool, url: str = "", error: str = "") -> None:
        if error:
            self._obs_status_key = "err"
            self._obs_status_err = error
            self._obs_status_url = ""
        elif running:
            self._obs_status_key = "on"
            self._obs_status_url = url
            self._obs_status_err = ""
        else:
            self._obs_status_key = "off"
            self._obs_status_url = ""
            self._obs_status_err = ""
        self._refresh_obs_status_label()

    def _refresh_obs_status_label(self) -> None:
        if self._obs_status_key == "on":
            self.obs_status_label.setText(tr("obs.status_on", url=self._obs_status_url))
        elif self._obs_status_key == "err":
            self.obs_status_label.setText(
                tr("obs.status_err", err=self._obs_status_err)
            )
        else:
            self.obs_status_label.setText(tr("obs.status_off"))

    def obs_url(self) -> str:
        return f"http://127.0.0.1:{int(self.obs_port_spin.value())}/"

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
