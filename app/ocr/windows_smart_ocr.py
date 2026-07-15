"""
Smart Windows OCR chain:
  1) OneOCR (Snipping Tool offline model via oneocr.dll) — best quality, no pack ID
  2) Windows AI TextRecognizer (WASDK) — often Access Denied when unpackaged
  3) Classic Windows.Media.Ocr
"""

from __future__ import annotations

from PIL import Image


class WindowsSmartOCREngine:
    name = "windows"

    def __init__(self, lang: str = "auto") -> None:
        self._preferred = lang or "auto"
        self._oneocr = None
        self._ai = None
        self._classic = None
        self._mode = "init"
        self._errors: list[str] = []

        try:
            from app.ocr.oneocr_engine import OneOCREngine, oneocr_available

            if oneocr_available():
                self._oneocr = OneOCREngine()
                self._mode = "oneocr"
        except Exception as exc:
            self._errors.append(f"oneocr: {exc}")
            self._oneocr = None

        try:
            from app.ocr.windows_ai_ocr import (
                WindowsAIOCREngine,
                windows_ai_ocr_available,
            )

            if windows_ai_ocr_available():
                self._ai = WindowsAIOCREngine(lang=self._preferred)
                if self._mode == "init":
                    self._mode = "ai"
        except Exception as exc:
            self._errors.append(f"ai: {exc}")
            self._ai = None

        from app.ocr.windows_ocr_engine import WindowsOCREngine

        self._classic = WindowsOCREngine(lang=self._preferred)
        if self._mode == "init":
            self._mode = "classic"

    @property
    def backend_label(self) -> str:
        labels = {
            "oneocr": "OneOCR（剪取工具離線模型）",
            "ai": "Windows AI OCR（剪取工具同款 API）",
            "classic": getattr(self._classic, "backend_label", "Windows Media.Ocr"),
            "classic_fallback": "Windows Media.Ocr（後援）",
        }
        return labels.get(self._mode, "Windows OCR")

    def recognize(self, image: Image.Image) -> str:
        if self._oneocr is not None:
            try:
                text = (self._oneocr.recognize(image) or "").strip()
                if text:
                    self._mode = "oneocr"
                    return text
            except Exception as exc:
                self._errors.append(f"oneocr run: {exc}")

        if self._ai is not None:
            try:
                text = (self._ai.recognize(image) or "").strip()
                if text:
                    self._mode = "ai"
                    return text
            except PermissionError as exc:
                self._errors.append(str(exc))
                self._ai = None
            except Exception as exc:
                self._errors.append(f"ai run: {exc}")

        assert self._classic is not None
        self._mode = "classic_fallback" if self._errors else "classic"
        return self._classic.recognize(image)
