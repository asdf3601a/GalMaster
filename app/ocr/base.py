"""OCR engine protocol and factory."""

from __future__ import annotations

from typing import Protocol

from PIL import Image

# Supported engines (UI + factory). Unknown / legacy kinds map to oneocr.
OCR_ENGINE_IDS: tuple[str, ...] = ("oneocr", "manga", "rapid", "paddle")
DEFAULT_OCR_ENGINE = "oneocr"


class OCREngine(Protocol):
    name: str

    def recognize(self, image: Image.Image) -> str: ...


def normalize_ocr_engine(kind: str | None) -> str:
    """Map legacy / unknown engine ids to a supported engine."""
    k = (kind or "").lower().strip()
    if k in OCR_ENGINE_IDS:
        return k
    # legacy aliases → default
    if k in (
        "auto",
        "hybrid",
        "windows",
        "winocr",
        "winrt",
        "windows_ai",
        "snip",
        "snipping",
        "windows_classic",
        "mediaocr",
        "snipping_oneocr",
        "win11_oneocr",
    ):
        return DEFAULT_OCR_ENGINE
    if k in ("rapidocr",):
        return "rapid"
    if k in ("paddleocr",):
        return "paddle"
    return DEFAULT_OCR_ENGINE


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
