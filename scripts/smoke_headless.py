"""Headless smoke: OCR + optional LLM (if XAI_API_KEY set)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    img = Image.new("RGB", (520, 140), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    sample = "こんにちは"
    # Prefer Japanese if font supports; fallback English
    try:
        draw.text((16, 40), sample, fill=(0, 0, 0), font=font)
    except Exception:
        sample = "GOOD MORNING"
        draw.text((16, 40), sample, fill=(0, 0, 0), font=font)

    # Always also draw English line for OCR reliability
    draw.rectangle((0, 0, 520, 140), fill=(255, 255, 255))
    draw.text((16, 40), "KONNICHIWA", fill=(0, 0, 0), font=font)

    from app.ocr.paddle_ocr_engine import PaddleOCREngine

    ocr = PaddleOCREngine(lang="en")
    print(f"[OCR] backend={ocr.backend_label}")
    text = ocr.recognize(img)
    print(f"[OCR] raw={text!r}")
    if not text.strip():
        print("[OCR] FAIL: empty")
        return 1
    print("[OCR] OK")

    key = os.environ.get("XAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        print("[LLM] SKIP: no XAI_API_KEY")
        return 0

    from app.translate.llm_translator import LLMTranslator

    tr = LLMTranslator(api_key=key)
    out = tr.translate(text, "en", "zh-Hant")
    print(f"[LLM] {out!r}")
    if not out.strip():
        print("[LLM] FAIL: empty")
        return 1
    print("[LLM] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
