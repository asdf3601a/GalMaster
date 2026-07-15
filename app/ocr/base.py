"""OCR engine protocol and factory."""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class OCREngine(Protocol):
    name: str

    def recognize(self, image: Image.Image) -> str: ...


def create_ocr_engine(kind: str = "auto", *, lang: str = "ja") -> OCREngine:
    """
    Create an OCR backend.

    kind: auto | manga | oneocr | windows | windows_ai | windows_classic | paddle | rapid
    lang: app source language (used by classic Windows OCR language selection)
    """
    kind = (kind or "auto").lower().strip()
    if kind in ("auto", "hybrid"):
        from .hybrid_ocr_engine import HybridOCREngine

        return HybridOCREngine()
    if kind == "manga":
        from .manga_ocr_engine import MangaOCREngine

        return MangaOCREngine()
    if kind in ("oneocr", "snipping_oneocr", "win11_oneocr"):
        from .oneocr_engine import OneOCREngine

        return OneOCREngine()
    if kind in ("windows", "winocr", "winrt"):
        # Smart: OneOCR (Snipping Tool model) → WASDK AI → Media.Ocr
        from .windows_smart_ocr import WindowsSmartOCREngine

        return WindowsSmartOCREngine(lang=lang or "auto")
    if kind in ("windows_ai", "snip", "snipping"):
        from .windows_ai_ocr import WindowsAIOCREngine

        return WindowsAIOCREngine(lang=lang or "auto")
    if kind in ("windows_classic", "mediaocr"):
        from .windows_ocr_engine import WindowsOCREngine

        return WindowsOCREngine(lang=lang or "auto")
    if kind in ("rapid", "rapidocr"):
        from .rapid_ocr_engine import RapidOCREngine

        return RapidOCREngine()
    if kind in ("paddle", "paddleocr"):
        from .paddle_ocr_engine import PaddleOCREngine

        return PaddleOCREngine()
    # Unknown → hybrid
    from .hybrid_ocr_engine import HybridOCREngine

    return HybridOCREngine()
