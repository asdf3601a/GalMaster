"""Always-on-top translucent translation overlay (display-only).

All settings (opacity, font, click-through, show/hide, style) live in the main window.
This window shows text; when click-through is off it can be moved and edge-resized.
"""

from __future__ import annotations

import ctypes
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr
from app.ui.styles import (
    OVERLAY_PANEL_STYLE,
    _css_font_family,
    overlay_panel_style,
)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
user32 = ctypes.windll.user32

# Edge hit thickness for frameless resize (logical px)
_RESIZE_MARGIN = 8

_EDGE_LEFT = 1
_EDGE_RIGHT = 2
_EDGE_TOP = 4
_EDGE_BOTTOM = 8


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
    geometry_changed = Signal()

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
        self.setMouseTracking(True)

        self._drag_pos: QPoint | None = None
        self._resize_edges = 0
        self._resize_origin_geo: QRect | None = None
        self._resize_origin_global: QPoint | None = None
        self._geo_at_press: QRect | None = None
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
        self.panel.setMouseTracking(True)
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
        # Edge resize must work even when the cursor is over child labels
        self._install_mouse_filters(self)

    def _install_mouse_filters(self, widget: QWidget) -> None:
        widget.setMouseTracking(True)
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

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

        self._refresh_panel_style()
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
        self._refresh_panel_style()

    def _refresh_panel_style(self) -> None:
        """Apply colors/fonts via QSS.

        App-level ``MAIN_STYLE`` sets ``font-size`` on ``QWidget``, which makes
        ``QLabel.setFont()`` and parent-cascaded sizes unreliable. Font size
        must be set on the labels' own stylesheets.
        """
        src_px = max(10, min(72, int(self._source_font_size or 14)))
        tr_px = max(10, min(72, int(self._translation_font_size or 16)))
        fam_css = _css_font_family(self._font_family)
        tr_weight = "700" if self._translation_bold else "400"

        self.panel.setStyleSheet(
            overlay_panel_style(
                bg_color=self._bg_color,
                bg_alpha=self._bg_alpha,
                source_color=self._source_color,
                translation_color=self._translation_color,
                source_font_size=src_px,
                translation_font_size=tr_px,
                font_family=self._font_family,
                translation_bold=self._translation_bold,
            )
        )
        # Label-local QSS (highest priority under app stylesheet).
        self.source_label.setStyleSheet(
            f"font-size: {src_px}px; font-weight: 400; color: {self._source_color}; "
            f"background: transparent; {fam_css}"
        )
        self.translation_label.setStyleSheet(
            f"font-size: {tr_px}px; font-weight: {tr_weight}; "
            f"color: {self._translation_color}; background: transparent; {fam_css}"
        )

    def _apply_fonts(self) -> None:
        self._refresh_panel_style()

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
        if self._click_through:
            self.unsetCursor()
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

        show_src = bool(self._show_source and self._last_source)
        if show_src:
            if self.source_label.text() != self._last_source:
                self.source_label.setText(self._last_source)
        else:
            if self.source_label.text():
                self.source_label.setText("")
        if self.source_label.isVisible() != show_src:
            self.source_label.setVisible(show_src)

        show_tr = bool(self._show_translation)
        tr_text = (self._last_translation or "…") if show_tr else ""
        if show_tr:
            if self.translation_label.text() != tr_text:
                self.translation_label.setText(tr_text)
        else:
            if self.translation_label.text():
                self.translation_label.setText("")
        if self.translation_label.isVisible() != show_tr:
            self.translation_label.setVisible(show_tr)

        if self.status_label.text() != (status or ""):
            self.status_label.setText(status or "")
        if show and not self.isVisible():
            self.show()

    def set_status(self, text: str) -> None:
        if self.status_label.text() != text:
            self.status_label.setText(text)

    def _hit_edges(self, pos: QPoint) -> int:
        """Return edge bitmask for local position."""
        edges = 0
        m = _RESIZE_MARGIN
        r = self.rect()
        if pos.x() <= m:
            edges |= _EDGE_LEFT
        elif pos.x() >= r.width() - m:
            edges |= _EDGE_RIGHT
        if pos.y() <= m:
            edges |= _EDGE_TOP
        elif pos.y() >= r.height() - m:
            edges |= _EDGE_BOTTOM
        return edges

    def _cursor_for_edges(self, edges: int) -> Qt.CursorShape:
        if edges in (_EDGE_LEFT | _EDGE_TOP, _EDGE_RIGHT | _EDGE_BOTTOM):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in (_EDGE_RIGHT | _EDGE_TOP, _EDGE_LEFT | _EDGE_BOTTOM):
            return Qt.CursorShape.SizeBDiagCursor
        if edges in (_EDGE_LEFT, _EDGE_RIGHT):
            return Qt.CursorShape.SizeHorCursor
        if edges in (_EDGE_TOP, _EDGE_BOTTOM):
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def _update_hover_cursor(self, pos: QPoint) -> None:
        if self._click_through:
            return
        edges = self._hit_edges(pos)
        self.setCursor(QCursor(self._cursor_for_edges(edges)))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """Edge resize + cursor even when the pointer is over child widgets.

        Interior left-drag still moves the window (via press interception on
        non-text chrome and self.mousePressEvent). Text labels keep selection
        unless the press starts on an edge.
        """
        if self._click_through:
            return super().eventFilter(watched, event)
        et = event.type()
        if not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)

        global_pos = event.globalPosition().toPoint()
        pos = self.mapFromGlobal(global_pos)

        # Active resize/move: keep handling until release
        if et == QEvent.Type.MouseMove:
            if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
                self._apply_resize(global_pos)
                return True
            if (
                self._drag_pos is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                self.move(global_pos - self._drag_pos)
                return True
            # Don't thrash cursor while user is selecting text
            if event.buttons() & Qt.MouseButton.LeftButton:
                return False
            if self.rect().contains(pos):
                self._update_hover_cursor(pos)
            return False

        if (
            et == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if not self.rect().contains(pos):
                return False
            edges = self._hit_edges(pos)
            if edges:
                self._geo_at_press = QRect(self.geometry())
                self._resize_edges = edges
                self._resize_origin_geo = QRect(self.geometry())
                self._resize_origin_global = global_pos
                self._drag_pos = None
                return True
            # Header / status chrome: start move (leave source/translation free)
            if watched in (
                self.title_label,
                self.hint_label,
                self.status_label,
                self.panel,
                self,
            ):
                self._geo_at_press = QRect(self.geometry())
                self._resize_edges = 0
                self._resize_origin_geo = None
                self._resize_origin_global = None
                self._drag_pos = global_pos - self.frameGeometry().topLeft()
                return True
            return False

        if (
            et == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            if (
                self._drag_pos is None
                and not self._resize_edges
                and self._geo_at_press is None
            ):
                return False
            moved = (
                self._geo_at_press is not None and self.geometry() != self._geo_at_press
            )
            self._drag_pos = None
            self._resize_edges = 0
            self._resize_origin_geo = None
            self._resize_origin_global = None
            self._geo_at_press = None
            if moved:
                self.geometry_changed.emit()
            if self.rect().contains(pos):
                self._update_hover_cursor(pos)
            return True

        return super().eventFilter(watched, event)

    def _apply_resize(self, global_pos: QPoint) -> None:
        assert self._resize_origin_geo is not None
        assert self._resize_origin_global is not None
        delta = global_pos - self._resize_origin_global
        geo = QRect(self._resize_origin_geo)
        min_w = self.minimumWidth()
        min_h = self.minimumHeight()
        edges = self._resize_edges

        left = geo.left()
        top = geo.top()
        right = geo.right()
        bottom = geo.bottom()

        if edges & _EDGE_LEFT:
            left = min(geo.left() + delta.x(), right - min_w + 1)
        if edges & _EDGE_RIGHT:
            right = max(geo.right() + delta.x(), left + min_w - 1)
        if edges & _EDGE_TOP:
            top = min(geo.top() + delta.y(), bottom - min_h + 1)
        if edges & _EDGE_BOTTOM:
            bottom = max(geo.bottom() + delta.y(), top + min_h - 1)

        self.setGeometry(QRect(QPoint(left, top), QPoint(right, bottom)))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # Fallback when press lands on the window chrome itself
        if (
            event.button() == Qt.MouseButton.LeftButton
            and not self._click_through
            and self._drag_pos is None
            and not self._resize_edges
        ):
            pos = event.position().toPoint()
            edges = self._hit_edges(pos)
            self._geo_at_press = QRect(self.geometry())
            if edges:
                self._resize_edges = edges
                self._resize_origin_geo = QRect(self.geometry())
                self._resize_origin_global = event.globalPosition().toPoint()
            else:
                self._drag_pos = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._click_through:
            return super().mouseMoveEvent(event)
        if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        self._update_hover_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and not self._click_through:
            moved = (
                self._geo_at_press is not None and self.geometry() != self._geo_at_press
            )
            if self._drag_pos is not None or self._resize_edges or self._geo_at_press:
                self._drag_pos = None
                self._resize_edges = 0
                self._resize_origin_geo = None
                self._resize_origin_global = None
                self._geo_at_press = None
                if moved:
                    self.geometry_changed.emit()
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if not self._resize_edges and not self._click_through:
            self.unsetCursor()
        super().leaveEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
