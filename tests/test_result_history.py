"""Result history clamp + log trim helpers."""

from __future__ import annotations

import pytest

from app.config import (
    RESULT_HISTORY_DEFAULT,
    AppConfig,
    clamp_result_history_lines,
)


def test_clamp_result_history_lines() -> None:
    assert clamp_result_history_lines(None) == RESULT_HISTORY_DEFAULT
    assert clamp_result_history_lines("bad") == RESULT_HISTORY_DEFAULT
    assert clamp_result_history_lines(0) == 1
    assert clamp_result_history_lines(12) == 12
    assert clamp_result_history_lines(100) == 100
    assert clamp_result_history_lines(999) == 100


def test_from_dict_clamps_result_history() -> None:
    c = AppConfig.from_dict({"result_history_lines": 500})
    assert c.result_history_lines == 100
    c2 = AppConfig.from_dict({})
    assert c2.result_history_lines == RESULT_HISTORY_DEFAULT


def test_format_result_line_concise() -> None:
    pytest.importorskip("PySide6")
    from app.app_controller import AppController
    from app.i18n import set_language

    set_language("zh-Hant")
    line = AppController._format_result_line(
        source="こんにちは\n世界",
        translation="你好\n世界",
    )
    assert "\n" not in line
    assert "→" in line
    assert "こんにちは" in line
    assert "你好" in line

    ocr = AppController._format_result_line(source="only ocr", ocr_only=True)
    assert "→" not in ocr
    assert "only ocr" in ocr

    err = AppController._format_result_line(source="x", err="timeout")
    assert "!" in err
    assert "timeout" in err
