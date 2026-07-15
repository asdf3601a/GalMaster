"""OneOCR (Snipping Tool offline model) tests — skip if ScreenSketch missing."""

from __future__ import annotations

import sys

import pytest
from PIL import Image, ImageDraw, ImageFont

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def _text_image(text: str, font_name: str = "msjh.ttc") -> Image.Image:
    img = Image.new("RGB", (640, 120), (20, 20, 30))
    draw = ImageDraw.Draw(img)
    font = None
    for name in (font_name, "msgothic.ttc", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((20, 30), text, fill=(255, 255, 255), font=font)
    return img


def test_oneocr_available_or_skip():
    from app.ocr.oneocr_engine import oneocr_available

    if not oneocr_available():
        pytest.skip("ScreenSketch OneOCR not installed")


def test_oneocr_reads_cjk_and_latin():
    from app.ocr.oneocr_engine import OneOCREngine, oneocr_available

    if not oneocr_available():
        pytest.skip("ScreenSketch OneOCR not installed")

    eng = OneOCREngine()
    try:
        zh = eng.recognize(_text_image("你好世界", "msjh.ttc"))
        assert "你好" in zh or "世界" in zh, zh

        ja = eng.recognize(_text_image("こんにちは", "msgothic.ttc"))
        assert "こん" in ja or "こんにちは" in ja, ja

        en = eng.recognize(_text_image("HELLO", "arial.ttf"))
        assert "HELLO" in en.upper().replace(" ", ""), en
    finally:
        eng.close()


def test_factory_oneocr():
    from app.ocr.base import create_ocr_engine
    from app.ocr.oneocr_engine import oneocr_available

    if not oneocr_available():
        pytest.skip("ScreenSketch OneOCR not installed")
    eng = create_ocr_engine("oneocr")
    assert eng.name == "oneocr"
