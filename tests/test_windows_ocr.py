"""Windows.Media.Ocr integration tests (skip if no OCR language pack)."""

from __future__ import annotations

import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _text_image(text: str, *, dark: bool = False) -> Image.Image:
    bg = (20, 20, 35) if dark else (255, 255, 255)
    fg = (240, 240, 250) if dark else (0, 0, 0)
    img = Image.new("RGB", (640, 120), color=bg)
    draw = ImageDraw.Draw(img)
    font = None
    for name in ("msgothic.ttc", "msjh.ttc", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((20, 30), text, fill=fg, font=font)
    return img


def test_list_languages_or_empty():
    from app.ocr.windows_ocr_engine import list_windows_ocr_languages

    langs = list_windows_ocr_languages()
    assert isinstance(langs, list)


def test_create_windows_engine_factory():
    from app.ocr.base import create_ocr_engine

    # Must work with user-profile engine alone (no extra pack install)
    eng = create_ocr_engine("windows", lang="auto")
    assert eng.name == "windows"
    label = getattr(eng, "backend_label", "")
    # May resolve to OneOCR / AI / classic depending on machine
    assert any(k in label for k in ("Windows", "OneOCR", "Media.Ocr", "OCR"))


def test_windows_ocr_user_profile_no_install():
    """System OCR works out of the box (user profile languages)."""
    from app.ocr.windows_ocr_engine import WindowsOCREngine, resolve_windows_ocr_tag

    tag = resolve_windows_ocr_tag("auto")
    assert tag  # any system tag / profile

    # Even if ja pack is missing, constructing for ja must not raise
    engine = WindowsOCREngine(lang="ja")
    assert engine.name == "windows"

    # Chinese / English usually work with the inbox profile engine on TW/US PCs
    zh = engine.recognize(_text_image("你好世界", dark=True))
    en = engine.recognize(_text_image("HELLO", dark=False))
    assert zh or en, f"expected some OCR output, got zh={zh!r} en={en!r}"
