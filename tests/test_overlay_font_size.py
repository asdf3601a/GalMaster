"""Overlay font sizes must honor config under app-wide MAIN_STYLE."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QFontMetrics  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.ui.overlay_window import OverlayWindow  # noqa: E402
from app.ui.styles import (  # noqa: E402
    MAIN_STYLE,
    apply_dark_palette,
    overlay_panel_style,
)


def test_overlay_panel_style_embeds_font_sizes() -> None:
    qss = overlay_panel_style(
        source_font_size=22,
        translation_font_size=36,
        translation_bold=True,
        font_family="Segoe UI",
    )
    assert "font-size: 22px" in qss
    assert "font-size: 36px" in qss
    assert 'font-family: "Segoe UI"' in qss
    assert "font-weight: 700" in qss


def test_overlay_font_size_applies_under_main_style(qapp) -> None:
    apply_dark_palette(qapp)
    qapp.setStyleSheet(MAIN_STYLE)

    ov = OverlayWindow()
    cfg = AppConfig()
    cfg.overlay_source_font_size = 28
    cfg.overlay_translation_font_size = 40
    cfg.overlay_translation_bold = True
    ov.apply_style(cfg)
    ov.show()
    qapp.processEvents()
    ov.source_label.ensurePolished()
    ov.translation_label.ensurePolished()

    src_h = QFontMetrics(ov.source_label.font()).height()
    tr_h = QFontMetrics(ov.translation_label.font()).height()
    # Must be larger than the app-default 13px metrics (~16px)
    assert ov.source_label.font().pixelSize() == 28
    assert ov.translation_label.font().pixelSize() == 40
    assert src_h >= 28
    assert tr_h >= 40
    assert tr_h > src_h

    assert "font-size: 28px" in ov.source_label.styleSheet()
    assert "font-size: 40px" in ov.translation_label.styleSheet()
    ov.close()
