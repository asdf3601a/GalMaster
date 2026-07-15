"""Smoke test OCR engines on synthetic images."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


def _text_image(
    text: str,
    *,
    dark: bool = False,
    size: tuple[int, int] = (640, 120),
    font_size: int = 42,
) -> Image.Image:
    bg = (20, 20, 35) if dark else (255, 255, 255)
    fg = (240, 240, 250) if dark else (0, 0, 0)
    img = Image.new("RGB", size, color=bg)
    draw = ImageDraw.Draw(img)
    font = None
    for name in ("msgothic.ttc", "YuGothM.ttc", "meiryo.ttc", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((20, 30), text, fill=fg, font=font)
    return img


def test_rapid_ocr_reads_simple_text():
    from app.ocr.rapid_ocr_engine import RapidOCREngine

    engine = RapidOCREngine()
    text = engine.recognize(_text_image("HELLO")).upper().replace(" ", "")
    assert "HELLO" in text or "HELL0" in text


def test_paddle_ocr_reads_simple_text():
    from app.ocr.paddle_ocr_engine import PaddleOCREngine

    engine = PaddleOCREngine(lang="en")
    text = engine.recognize(_text_image("HELLO")).upper().replace(" ", "")
    assert "HELLO" in text or "HELL0" in text


def test_hybrid_ocr_reads_english_and_japanese():
    from app.ocr.hybrid_ocr_engine import HybridOCREngine

    engine = HybridOCREngine()
    en = engine.recognize(_text_image("HELLO WORLD", dark=True))
    assert "HELLO" in en.upper() or "WORLD" in en.upper(), en

    ja = engine.recognize(_text_image("こんにちは", dark=True))
    # manga-ocr should get hiragana correctly
    assert "こん" in ja or "こんにちは" in ja or "コン" in ja, ja


def test_create_ocr_default_is_auto():
    from app.ocr.base import create_ocr_engine

    eng = create_ocr_engine("auto")
    assert eng.name == "auto"
