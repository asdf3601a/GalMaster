"""Fullscreen drag region selector."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionSelector(QWidget):
    """Modal fullscreen overlay; emit selected screen rect (x, y, w, h)."""

    region_selected = Signal(int, int, int, int)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._virtual = QRect()

    def start(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            self.cancelled.emit()
            return
        # Cover entire virtual desktop
        left = min(s.geometry().left() for s in screens)
        top = min(s.geometry().top() for s in screens)
        right = max(s.geometry().right() for s in screens)
        bottom = max(s.geometry().bottom() for s in screens)
        self._virtual = QRect(left, top, right - left + 1, bottom - top + 1)
        self.setGeometry(self._virtual)
        self._origin = None
        self._current = None
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()
        self.grabMouse()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        if self._origin and self._current:
            rect = QRect(self._origin, self._current).normalized()
            # Clear selection area
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver
            )
            pen = QPen(QColor(100, 140, 255), 2)
            painter.setPen(pen)
            painter.drawRect(rect.adjusted(0, 0, -1, -1))
            # Size label
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                rect.left() + 6,
                rect.top() - 8 if rect.top() > 20 else rect.top() + 18,
                f"{rect.width()} × {rect.height()}",
            )
        else:
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "拖曳框選 OCR 區域（Esc 取消）",
            )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._origin
            and self._current
        ):
            rect = QRect(self._origin, self._current).normalized()
            self._finish()
            if rect.width() >= 4 and rect.height() >= 4:
                # Widget-local → Qt global logical (DIP) screen coords
                sx = self._virtual.left() + rect.left()
                sy = self._virtual.top() + rect.top()
                # Convert to physical pixels for mss / Win32 capture
                from app.capture.dpi import qt_rect_to_physical

                px, py, pw, ph = qt_rect_to_physical(
                    sx, sy, rect.width(), rect.height()
                )
                self.region_selected.emit(px, py, pw, ph)
            else:
                self.cancelled.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._finish()
            self.cancelled.emit()

    def _finish(self) -> None:
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        try:
            self.releaseMouse()
        except Exception:
            pass
        self.hide()
