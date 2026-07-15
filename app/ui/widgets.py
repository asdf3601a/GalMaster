"""Shared Qt widgets with scroll-friendly behavior."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class NoWheelComboBox(QComboBox):
    """Ignore wheel unless focused so parent QScrollArea can scroll."""

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()
