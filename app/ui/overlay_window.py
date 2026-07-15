"""Always-on-top translucent translation overlay (display-only).

All settings (opacity, font, click-through, show/hide) live in the main window.
This window only shows text and can be dragged when click-through is off.
"""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ui.styles import OVERLAY_PANEL_STYLE

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
        self._font_size = 16

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
        self.hint_label = QLabel("設定請在主程式調整")
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

        self.translation_label = QLabel("框選區域後按熱鍵翻譯")
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
        self.apply_font_size(self._font_size)

    def apply_font_size(self, size: int) -> None:
        self._font_size = int(size)
        f = QFont()
        f.setPointSize(max(10, self._font_size - 2))
        self.source_label.setFont(f)
        f2 = QFont()
        f2.setPointSize(self._font_size)
        f2.setBold(True)
        self.translation_label.setFont(f2)

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
            "滑鼠穿透中 · 設定在主程式" if self._click_through else "設定請在主程式調整"
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
        self.source_label.setText(source)
        self.source_label.setVisible(bool(source))
        self.translation_label.setText(translation or "…")
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
