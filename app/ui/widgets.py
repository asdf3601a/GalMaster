"""Shared Qt widgets with scroll-friendly behavior."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class NoWheelComboBox(QComboBox):
    """Never change selection via mouse wheel (page scroll still works)."""

    def wheelEvent(self, event) -> None:  # noqa: N802
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
