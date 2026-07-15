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
    selection-background-color: #5b6cff;
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

OVERLAY_PANEL_STYLE = """
QFrame#panel {
    background-color: rgba(20, 20, 28, 210);
    border: 1px solid rgba(120, 140, 255, 160);
    border-radius: 10px;
}
QLabel#title {
    color: #9eb0ff;
    font-size: 11px;
    font-weight: 600;
}
QLabel#source {
    color: #c8c8d8;
    font-size: 13px;
}
QLabel#translation {
    color: #ffffff;
    font-weight: 600;
}
QPushButton#overlayBtn {
    background-color: rgba(80, 90, 160, 180);
    color: white;
    border: none;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
}
QPushButton#overlayBtn:hover {
    background-color: rgba(100, 110, 200, 220);
}
"""
