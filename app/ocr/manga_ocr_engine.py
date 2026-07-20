"""manga-ocr backend — strong on Japanese game / manga text."""

from __future__ import annotations

from PIL import Image

from app.ocr.preprocess import preprocess_for_ocr


class MangaOCREngine:
    name = "manga"

    def __init__(self) -> None:
        try:
            from manga_ocr import MangaOcr
        except ImportError as exc:
            raise ImportError("manga-ocr 未安裝。請執行: uv sync") from exc
        self._ocr = MangaOcr()

    @property
    def backend_label(self) -> str:
        return "manga-ocr"

    def recognize(self, image: Image.Image) -> str:
        best = ""
        for force_invert in (None, False, True):
            img = preprocess_for_ocr(image, force_invert=force_invert)
            try:
                text = (self._ocr(img.convert("RGB")) or "").strip()
            except Exception:
                text = ""
            if len(text) > len(best):
                best = text
        return best
