"""Shared Qt widgets with scroll-friendly behavior."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
)

# Match combo visual height under MAIN_STYLE; QSS splits into two flush +/- buttons.
_SPIN_FIXED_H = 36


def _compact_spin(widget: QAbstractSpinBox) -> None:
    """Keep +/- stacked flush; prevent layout from stretching the control taller."""
    widget.setFixedHeight(_SPIN_FIXED_H)
    widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    widget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)


class NoWheelComboBox(QComboBox):
    """Never change selection via mouse wheel (page scroll still works)."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        _compact_spin(self)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        _compact_spin(self)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelFontComboBox(QFontComboBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


class ColorButton(QPushButton):
    """Small button that stores a #RRGGBB color and opens QColorDialog."""

    color_changed = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(56)
        self.setObjectName("secondary")
        self._color = "#ffffff"
        self.set_color(color)
        self.clicked.connect(self._pick)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        c = (color or "#ffffff").strip()
        if not c.startswith("#"):
            c = "#" + c
        if len(c) == 4:
            c = "#" + "".join(ch * 2 for ch in c[1:])
        if len(c) != 7:
            c = "#ffffff"
        self._color = c.lower()
        # Readable label contrast
        r, g, b = (
            int(self._color[1:3], 16),
            int(self._color[3:5], 16),
            int(self._color[5:7], 16),
        )
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg = "#111111" if lum > 160 else "#ffffff"
        self.setText(self._color)
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; color: {fg}; "
            f"border: 1px solid #666; border-radius: 4px; padding: 2px 4px; font-size: 10px; }}"
        )

    def _pick(self) -> None:
        initial = QColor(self._color)
        chosen = QColorDialog.getColor(initial, self)
        if chosen.isValid():
            old = self._color
            self.set_color(chosen.name())
            if self._color != old:
                self.color_changed.emit(self._color)
