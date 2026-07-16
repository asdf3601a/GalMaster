"""Shared Qt style snippets."""

MAIN_STYLE = """
QMainWindow, QWidget {
    background-color: #1e1e24;
    color: #e8e8ef;
    font-size: 13px;
}
QGroupBox {
    border: 1px solid #3a3a48;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #a8b0ff;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {
    background-color: #2a2a34;
    border: 1px solid #45455a;
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 18px;
    selection-background-color: #5b6cff;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #c8c8d8;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #2a2a34;
    border: 1px solid #45455a;
    selection-background-color: #5b6cff;
    color: #e8e8ef;
    outline: none;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #353545;
    border: none;
    width: 18px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #45455a;
}
QPushButton {
    background-color: #4c5fff;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton:hover { background-color: #5d6fff; }
QPushButton:pressed { background-color: #3b4ce0; }
QPushButton:disabled { background-color: #3a3a48; color: #888; }
QPushButton#secondary {
    background-color: #3a3a48;
}
QPushButton#secondary:hover { background-color: #4a4a5a; }
QPushButton#danger {
    background-color: #a33;
}
QPushButton#danger:hover { background-color: #c44; }
QCheckBox { spacing: 8px; }
QStatusBar { background: #16161c; color: #aaa; }
QLabel#hint { color: #9999aa; font-size: 11px; }
QLabel#workStatus {
    background-color: #252536;
    border: 1px solid #45455a;
    border-radius: 8px;
    padding: 10px 12px;
    color: #d0d4ff;
    font-size: 13px;
    font-weight: 600;
}
QLabel#workStatus[busy="true"] {
    border-color: #5b6cff;
    color: #ffffff;
    background-color: #2a2f55;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: #1e1e24;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #45455a;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
"""

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = (hex_color or "#000000").lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return 20, 20, 28


def overlay_panel_style(
    *,
    bg_color: str = "#14141c",
    bg_alpha: int = 210,
    source_color: str = "#c8c8d8",
    translation_color: str = "#ffffff",
) -> str:
    """Build overlay panel stylesheet from runtime colors."""
    r, g, b = _hex_to_rgb(bg_color)
    a = max(0, min(255, int(bg_alpha)))
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
}}
QLabel#source {{
    color: {source_color};
}}
QLabel#translation {{
    color: {translation_color};
}}
"""


# Default static style (tests / fallback)
OVERLAY_PANEL_STYLE = overlay_panel_style()
