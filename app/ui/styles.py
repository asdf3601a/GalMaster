"""Shared Qt style snippets.

Windows note: QSS ``image: url(...)`` must use a plain filesystem path with
forward slashes (e.g. ``C:/.../arrow.png``). ``file:///`` URIs do **not** load
on Windows Qt stylesheets — arrows render as empty strips.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPolygon

_ASSETS = Path(__file__).resolve().parent / "assets"


def _qss_path(path: Path) -> str:
    """Path form that works inside QSS url() on Windows."""
    return path.resolve().as_posix()


def ensure_indicator_assets() -> tuple[Path, Path, Path, Path]:
    """Create PNG indicator icons if missing (works offline, no SVG/QSS quirks)."""
    _ASSETS.mkdir(parents=True, exist_ok=True)
    down = _ASSETS / "arrow-down.png"
    up = _ASSETS / "arrow-up.png"
    cb_off = _ASSETS / "checkbox-unchecked.png"
    cb_on = _ASSETS / "checkbox-checked.png"
    if not down.is_file() or not up.is_file():
        _write_triangle_png(down, up=False)
        _write_triangle_png(up, up=True)
    if not cb_off.is_file() or not cb_on.is_file():
        _write_checkbox_png(cb_off, checked=False)
        _write_checkbox_png(cb_on, checked=True)
    return down, up, cb_off, cb_on


def _write_triangle_png(path: Path, *, up: bool, size: int = 12) -> None:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#c8c8d8"))
    p.setPen(Qt.PenStyle.NoPen)
    m = 2
    if up:
        pts = [QPoint(size // 2, m), QPoint(size - m, size - m), QPoint(m, size - m)]
    else:
        pts = [QPoint(m, m), QPoint(size - m, m), QPoint(size // 2, size - m)]
    p.drawPolygon(QPolygon(pts))
    p.end()
    img.save(str(path))


def _write_checkbox_png(path: Path, *, checked: bool, size: int = 16) -> None:
    """Full checkbox glyph (box + optional check) so Fusion does not draw a mid-line."""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if checked:
        p.setBrush(QColor("#5b6cff"))
        p.setPen(QPen(QColor("#5b6cff"), 1))
    else:
        p.setBrush(QColor("#2a2a34"))
        p.setPen(QPen(QColor("#45455a"), 1))
    # inset so 1px stroke fits inside the bitmap
    p.drawRoundedRect(1, 1, size - 3, size - 3, 2, 2)
    if checked:
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPoint(4, size // 2), QPoint(size // 2 - 1, size - 5))
        p.drawLine(QPoint(size // 2 - 1, size - 5), QPoint(size - 4, 4))
    p.end()
    img.save(str(path))


def apply_dark_palette(app) -> None:
    """Fusion-friendly dark palette so unstyled parts stay readable."""
    pal = QPalette()
    window = QColor("#1e1e24")
    base = QColor("#2a2a34")
    alt = QColor("#252536")
    text = QColor("#e8e8ef")
    disabled = QColor("#888888")
    highlight = QColor("#5b6cff")
    button = QColor("#3a3a48")

    pal.setColor(QPalette.ColorRole.Window, window)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, alt)
    pal.setColor(QPalette.ColorRole.ToolTipBase, base)
    pal.setColor(QPalette.ColorRole.ToolTipText, text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, button)
    pal.setColor(QPalette.ColorRole.ButtonText, text)
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link, highlight)
    pal.setColor(QPalette.ColorRole.Highlight, highlight)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    app.setPalette(pal)


def build_main_style() -> str:
    """Main window stylesheet (designed for Fusion + dark palette)."""
    down_path, up_path, cb_off_path, cb_on_path = ensure_indicator_assets()
    # CRITICAL: plain path, not file:/// — see module docstring
    arrow_down = _qss_path(down_path)
    arrow_up = _qss_path(up_path)
    cb_off = _qss_path(cb_off_path)
    cb_on = _qss_path(cb_on_path)
    return f"""
/* Base */
QMainWindow, QDialog, QWidget {{
    background-color: #1e1e24;
    color: #e8e8ef;
    font-size: 13px;
}}
QGroupBox {{
    border: 1px solid #3a3a48;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #a8b0ff;
}}
QLabel {{
    color: #e8e8ef;
    background: transparent;
}}
QLabel#hint {{
    color: #9999aa;
    font-size: 11px;
    font-weight: 400;
}}

/* Text fields */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: #2a2a34;
    border: 1px solid #45455a;
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 18px;
    selection-background-color: #5b6cff;
    selection-color: #ffffff;
    color: #e8e8ef;
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: #888;
    background-color: #25252e;
}}

/* ----- ComboBox ----- */
QComboBox {{
    background-color: #2a2a34;
    border: 1px solid #45455a;
    border-radius: 6px;
    padding: 5px 8px 5px 8px;
    padding-right: 28px;
    min-height: 24px;
    selection-background-color: #5b6cff;
    selection-color: #ffffff;
    color: #e8e8ef;
}}
QComboBox:hover {{
    border-color: #5b6cff;
}}
QComboBox:disabled {{
    color: #888;
    background-color: #25252e;
}}
QComboBox::drop-down {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    border-left: 1px solid #45455a;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #353545;
}}
QComboBox::drop-down:hover {{
    background-color: #45455a;
}}
QComboBox::down-arrow {{
    image: url("{arrow_down}");
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: #2a2a34;
    border: 1px solid #45455a;
    selection-background-color: #5b6cff;
    selection-color: #ffffff;
    color: #e8e8ef;
    outline: none;
    padding: 4px;
}}

/* ----- SpinBox / DoubleSpinBox -----
   Fixed height in NoWheelSpinBox (34px) matches combo/line-edit.
   Two 16px buttons fill the right side with a shared 1px mid border. */
QSpinBox, QDoubleSpinBox {{
    background-color: #2a2a34;
    border: 1px solid #45455a;
    border-radius: 6px;
    padding: 5px 8px 5px 8px;
    padding-right: 24px;
    min-height: 24px;
    selection-background-color: #5b6cff;
    selection-color: #ffffff;
    color: #e8e8ef;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: #5b6cff;
}}
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: #888;
    background-color: #25252e;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    height: 16px;
    margin: 0px;
    padding: 0px;
    background-color: #353545;
    border: none;
    border-left: 1px solid #45455a;
    border-bottom: 1px solid #45455a;
    border-top-right-radius: 5px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    height: 16px;
    margin: 0px;
    padding: 0px;
    background-color: #353545;
    border: none;
    border-left: 1px solid #45455a;
    border-bottom-right-radius: 5px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: #45455a;
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background-color: #5b6cff;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{arrow_up}");
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{arrow_down}");
    width: 10px;
    height: 10px;
}}

/* Buttons */
QPushButton {{
    background-color: #4c5fff;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 600;
    min-height: 18px;
}}
QPushButton:hover {{ background-color: #5d6fff; }}
QPushButton:pressed {{ background-color: #3b4ce0; }}
QPushButton:disabled {{ background-color: #3a3a48; color: #888; }}
QPushButton#secondary {{
    background-color: #3a3a48;
    color: #e8e8ef;
}}
QPushButton#secondary:hover {{ background-color: #4a4a5a; }}
QPushButton#danger {{
    background-color: #a33;
}}
QPushButton#danger:hover {{ background-color: #c44; }}

/* Checkbox — full PNG indicators (border/fill in bitmap; avoids Fusion mid-line glitch) */
QCheckBox {{
    spacing: 8px;
    color: #e8e8ef;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
}}
QCheckBox::indicator:unchecked {{
    image: url("{cb_off}");
}}
QCheckBox::indicator:checked {{
    image: url("{cb_on}");
}}
QCheckBox::indicator:unchecked:disabled {{
    image: url("{cb_off}");
}}
QCheckBox::indicator:checked:disabled {{
    image: url("{cb_on}");
}}

QStatusBar {{
    background: #16161c;
    color: #aaa;
}}
QTextEdit#resultLog {{
    font-family: "Segoe UI", "Microsoft JhengHei UI", "Noto Sans CJK TC", sans-serif;
    font-size: 12px;
    font-weight: 400;
    padding: 8px 10px;
}}
QLabel#workStatus {{
    background-color: #252536;
    border: 1px solid #45455a;
    border-radius: 8px;
    padding: 10px 12px;
    color: #d0d4ff;
    font-size: 13px;
    font-weight: 600;
}}
QLabel#workStatus[busy="true"] {{
    border-color: #5b6cff;
    color: #ffffff;
    background-color: #2a2f55;
}}
QLabel#previewBox {{
    background: #1a1a22;
    border: 1px solid #333;
    border-radius: 6px;
    color: #888;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: #1e1e24;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #45455a;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #5b6cff;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}
QScrollBar:horizontal {{
    background: #1e1e24;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: #45455a;
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QToolTip {{
    background-color: #2a2a34;
    color: #e8e8ef;
    border: 1px solid #45455a;
    padding: 4px 8px;
}}
QMenu {{
    background-color: #2a2a34;
    color: #e8e8ef;
    border: 1px solid #45455a;
}}
QMenu::item:selected {{
    background-color: #5b6cff;
}}
"""


def build_main_style_safe() -> str:
    """Build style; tolerate missing Qt GUI during import in odd contexts."""
    try:
        return build_main_style()
    except Exception:
        # Fallback without arrow images (should not happen under normal GUI)
        return """
QMainWindow, QWidget { background-color: #1e1e24; color: #e8e8ef; font-size: 13px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {
    background-color: #2a2a34; border: 1px solid #45455a; border-radius: 6px;
    padding: 6px 8px; color: #e8e8ef;
}
QPushButton { background-color: #4c5fff; color: white; border: none; border-radius: 6px; padding: 8px 14px; }
"""


# Built when first needed if Qt is available; module import under tests may run headless.
try:
    MAIN_STYLE = build_main_style()
except Exception:
    MAIN_STYLE = build_main_style_safe()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = (hex_color or "#000000").lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return 20, 20, 28


def _css_font_family(family: str) -> str:
    """QSS font-family clause, or empty if no family selected."""
    fam = (family or "").strip()
    if not fam:
        return ""
    safe = fam.replace("\\", "\\\\").replace('"', '\\"')
    return f'font-family: "{safe}";'


def overlay_panel_style(
    *,
    bg_color: str = "#14141c",
    bg_alpha: int = 210,
    source_color: str = "#c8c8d8",
    translation_color: str = "#ffffff",
    source_font_size: int = 14,
    translation_font_size: int = 16,
    font_family: str = "",
    translation_bold: bool = True,
) -> str:
    """Build overlay panel stylesheet from runtime colors/fonts.

    Font sizes must live in QSS: app-level ``MAIN_STYLE`` sets ``font-size`` on
    ``QWidget``, which otherwise overrides ``QLabel.setFont()`` point sizes.
    """
    r, g, b = _hex_to_rgb(bg_color)
    a = max(0, min(255, int(bg_alpha)))
    src_px = max(10, min(72, int(source_font_size or 14)))
    tr_px = max(10, min(72, int(translation_font_size or 16)))
    fam = _css_font_family(font_family)
    tr_weight = "700" if translation_bold else "400"
    return f"""
QFrame#panel {{
    background-color: rgba({r}, {g}, {b}, {a});
    border: 1px solid rgba(120, 140, 255, 160);
    border-radius: 10px;
}}
QLabel#title {{
    color: #9eb0ff;
    font-size: 11px;
    font-weight: 600;
    background: transparent;
}}
QLabel#source {{
    color: {source_color};
    background: transparent;
    font-size: {src_px}px;
    font-weight: 400;
    {fam}
}}
QLabel#translation {{
    color: {translation_color};
    background: transparent;
    font-size: {tr_px}px;
    font-weight: {tr_weight};
    {fam}
}}
"""


# Default static style (tests / fallback)
OVERLAY_PANEL_STYLE = overlay_panel_style()
