"""Always-on-top translucent translation overlay (display-only).

All settings (opacity, font, click-through, show/hide, style) live in the main window.
This window only shows text and can be dragged when click-through is off.
"""

from __future__ import annotations

import ctypes
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ui.styles import OVERLAY_PANEL_STYLE, overlay_panel_style

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
user32 = ctypes.windll.user32


def _set_click_through(hwnd: int, enable: bool) -> None:
    # Use pointer-sized APIs on 64-bit Windows
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    style = get_long(hwnd, GWL_EXSTYLE)
    if enable:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        style |= WS_EX_LAYERED
        style &= ~WS_EX_TRANSPARENT
    set_long(hwnd, GWL_EXSTYLE, style)


class OverlayWindow(QWidget):
    """Presentation surface only — no settings controls."""

    closed = Signal()
    visibility_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowTitle("GalMaster Overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(240, 120)

        self._drag_pos: QPoint | None = None
        self._click_through = False
        self._show_source = True
        self._show_translation = True
        self._font_family = ""
        self._source_font_size = 14
        self._translation_font_size = 16
        self._source_color = "#c8c8d8"
        self._translation_color = "#ffffff"
        self._translation_bold = True
        self._text_align = "left"
        self._bg_color = "#14141c"
        self._bg_alpha = 210
        self._last_source = ""
        self._last_translation = ""
        self._last_status = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame()
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet(OVERLAY_PANEL_STYLE)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self.title_label = QLabel("GalMaster")
        self.title_label.setObjectName("title")
        header.addWidget(self.title_label)
        header.addStretch()
        # Hint only — settings are in the main window
        self.hint_label = QLabel(tr("overlay.hint"))
        self.hint_label.setObjectName("title")
        header.addWidget(self.hint_label)
        layout.addLayout(header)

        self.source_label = QLabel("")
        self.source_label.setObjectName("source")
        self.source_label.setWordWrap(True)
        self.source_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.source_label)

        self.translation_label = QLabel("…")
        self.translation_label.setObjectName("translation")
        self.translation_label.setWordWrap(True)
        self.translation_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.translation_label)

        self.status_label = QLabel("")
        self.status_label.setObjectName("title")
        layout.addWidget(self.status_label)

        root.addWidget(self.panel)
        self._apply_fonts()
        self._apply_alignment()

    def apply_style(self, style: Any = None, **kwargs: Any) -> None:
        """Apply display/style options from AppConfig or a dict-like / kwargs."""
        def _get(name: str, default: Any) -> Any:
            if name in kwargs:
                return kwargs[name]
            if style is None:
                return default
            if isinstance(style, dict):
                return style.get(name, default)
            return getattr(style, name, default)

        self._show_source = bool(_get("overlay_show_source", self._show_source))
        self._show_translation = bool(
            _get("overlay_show_translation", self._show_translation)
        )
        if not self._show_source and not self._show_translation:
            self._show_translation = True
        self._font_family = str(_get("overlay_font_family", self._font_family) or "")
        try:
            self._source_font_size = max(
                10, int(_get("overlay_source_font_size", self._source_font_size) or 14)
            )
        except (TypeError, ValueError):
            self._source_font_size = 14
        try:
            self._translation_font_size = max(
                10,
                int(
                    _get("overlay_translation_font_size", self._translation_font_size)
                    or 16
                ),
            )
        except (TypeError, ValueError):
            self._translation_font_size = 16
        self._source_color = str(
            _get("overlay_source_color", self._source_color) or "#c8c8d8"
        )
        self._translation_color = str(
            _get("overlay_translation_color", self._translation_color) or "#ffffff"
        )
        self._translation_bold = bool(
            _get("overlay_translation_bold", self._translation_bold)
        )
        align = str(_get("overlay_text_align", self._text_align) or "left").lower()
        self._text_align = align if align in ("left", "center") else "left"
        self._bg_color = str(_get("overlay_bg_color", self._bg_color) or "#14141c")
        try:
            self._bg_alpha = max(
                0, min(255, int(_get("overlay_bg_alpha", self._bg_alpha)))
            )
        except (TypeError, ValueError):
            self._bg_alpha = 210

        self.panel.setStyleSheet(
            overlay_panel_style(
                bg_color=self._bg_color,
                bg_alpha=self._bg_alpha,
                source_color=self._source_color,
                translation_color=self._translation_color,
            )
        )
        self._apply_fonts()
        self._apply_alignment()
        # Re-apply content visibility under new flags
        self.set_content(
            source=self._last_source,
            translation=self._last_translation,
            status=self._last_status,
            show=False,
        )

    def apply_font_size(self, size: int) -> None:
        """Legacy: set translation size and source ≈ size-2."""
        size = max(10, int(size))
        self._translation_font_size = size
        self._source_font_size = max(10, size - 2)
        self._apply_fonts()

    def _apply_fonts(self) -> None:
        fam = (self._font_family or "").strip()
        f_src = QFont()
        if fam:
            f_src.setFamily(fam)
        f_src.setPointSize(self._source_font_size)
        self.source_label.setFont(f_src)

        f_tr = QFont()
        if fam:
            f_tr.setFamily(fam)
        f_tr.setPointSize(self._translation_font_size)
        f_tr.setBold(bool(self._translation_bold))
        self.translation_label.setFont(f_tr)

    def _apply_alignment(self) -> None:
        if self._text_align == "center":
            flag = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        else:
            flag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        self.source_label.setAlignment(flag)
        self.translation_label.setAlignment(flag)

    def set_opacity_level(self, opacity: float) -> None:
        self.setWindowOpacity(max(0.3, min(1.0, float(opacity))))

    def set_click_through(self, enable: bool) -> None:
        """Applied only from main-window settings (Apply/Save/load)."""
        self._click_through = bool(enable)
        hwnd = int(self.winId())
        if hwnd:
            _set_click_through(hwnd, self._click_through)
        # When click-through, text selection / drag is off; show a light hint
        self.hint_label.setText(
            tr("overlay.hint_clickthrough")
            if self._click_through
            else tr("overlay.hint")
        )

    def retranslate(self) -> None:
        self.hint_label.setText(
            tr("overlay.hint_clickthrough")
            if self._click_through
            else tr("overlay.hint")
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Re-apply after winId is valid
        if self._click_through:
            _set_click_through(int(self.winId()), True)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def set_content(
        self,
        *,
        source: str = "",
        translation: str = "",
        status: str = "",
        show: bool = True,
    ) -> None:
        self._last_source = source or ""
        self._last_translation = translation or ""
        self._last_status = status or ""

        if self._show_source and self._last_source:
            self.source_label.setText(self._last_source)
            self.source_label.setVisible(True)
        else:
            self.source_label.setText("")
            self.source_label.setVisible(False)

        if self._show_translation:
            self.translation_label.setText(self._last_translation or "…")
            self.translation_label.setVisible(True)
        else:
            self.translation_label.setText("")
            self.translation_label.setVisible(False)

        self.status_label.setText(status)
        if show and not self.isVisible():
            self.show()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._click_through:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_pos = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
