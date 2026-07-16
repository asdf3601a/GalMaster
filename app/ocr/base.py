"""OCR engine protocol and factory."""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from app.config import (
    DEFAULT_OCR_ENGINE,
    OCR_ENGINE_IDS,
    normalize_ocr_engine_id,
)

# Re-export for UI / callers (canonical ids live in config as pure strings).
__all__ = [
    "OCR_ENGINE_IDS",
    "DEFAULT_OCR_ENGINE",
    "OCREngine",
    "normalize_ocr_engine",
    "create_ocr_engine",
]


class OCREngine(Protocol):
    name: str

    def recognize(self, image: Image.Image) -> str: ...


def normalize_ocr_engine(kind: str | None) -> str:
    """Map legacy / unknown engine ids to a supported engine."""
    return normalize_ocr_engine_id(kind)


def create_ocr_engine(kind: str = DEFAULT_OCR_ENGINE, *, lang: str = "ja") -> OCREngine:
    """
    Create an OCR backend.

    kind: oneocr | manga | rapid | paddle
    lang: app source language (used by paddle language selection)
    """
    kind = normalize_ocr_engine(kind)
    if kind == "manga":
        from .manga_ocr_engine import MangaOCREngine

        return MangaOCREngine()
    if kind == "oneocr":
        from .oneocr_engine import OneOCREngine

        return OneOCREngine()
    if kind == "rapid":
        from .rapid_ocr_engine import RapidOCREngine

        return RapidOCREngine()
    if kind == "paddle":
        from .paddle_ocr_engine import PaddleOCREngine

        return PaddleOCREngine(lang=lang or "ja")
    from .oneocr_engine import OneOCREngine

    return OneOCREngine()
