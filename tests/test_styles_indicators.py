"""GUI stylesheet indicator assets (combo/spin arrows)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from app.ui.styles import (  # noqa: E402
    _ASSETS,
    MAIN_STYLE,
    ensure_indicator_assets,
)


def test_indicator_pngs_exist() -> None:
    down, up, cb_off, cb_on = ensure_indicator_assets()
    for p in (down, up, cb_off, cb_on):
        assert p.is_file()
        assert p.stat().st_size > 20
    assert (_ASSETS / "arrow-down.png").is_file()
    assert (_ASSETS / "checkbox-unchecked.png").is_file()
    assert (_ASSETS / "checkbox-checked.png").is_file()


def test_main_style_uses_plain_path_not_file_uri() -> None:
    """Windows QSS does not load file:/// image urls for control indicators."""
    assert "file:///" not in MAIN_STYLE
    assert "arrow-down.png" in MAIN_STYLE
    assert "arrow-up.png" in MAIN_STYLE
    assert "checkbox-checked.png" in MAIN_STYLE
    assert "checkbox-unchecked.png" in MAIN_STYLE
    # path should look like C:/... or /... not quoted file scheme
    assert 'url("file:' not in MAIN_STYLE


def test_assets_dir_is_under_package() -> None:
    assert _ASSETS.name == "assets"
    assert _ASSETS.parent.name == "ui"
