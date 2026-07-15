"""Hybrid OCR for galgame dialogs: manga-ocr + PP-OCR detection/recognition."""

from __future__ import annotations

import re

import numpy as np
from PIL import Image

from app.ocr.preprocess import preprocess_for_ocr


_GARBAGE_RE = re.compile(r"^[\s□■▪▫○●◦‧·\.\-_=~`|\\/]+$")
_FULLWIDTH_RE = re.compile(r"[\uff01-\uff5e]")


def _clean(text: str) -> str:
    s = (text or "").strip()
    if not s or _GARBAGE_RE.match(s):
        return ""
    boxes = s.count("□") + s.count("■")
    if boxes and boxes >= max(1, len(s.replace(" ", "")) * 0.6):
        return ""
    return s


def _score(text: str) -> float:
    """Higher is better. Prefer Japanese/alphanumeric content, penalize garbage."""
    if not text:
        return -1.0
    s = text.replace("\n", "").replace(" ", "")
    if not s:
        return -1.0
    cjk = sum(1 for ch in s if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff")
    alnum = sum(1 for ch in s if ch.isalnum())
    fullw = len(_FULLWIDTH_RE.findall(s))
    # Prefer real CJK/alnum; penalize fullwidth latin (manga often fullwidth-izes EN poorly)
    return float(len(s) + 2 * cjk + alnum - 1.5 * fullw)


def _box_order_key(box) -> tuple[float, float]:
    try:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        return (float(min(ys)), float(min(xs)))
    except Exception:
        return (0.0, 0.0)


def _crop_box(img: Image.Image, box, pad: int = 6) -> Image.Image:
    xs = [int(p[0]) for p in box]
    ys = [int(p[1]) for p in box]
    x1, x2 = max(0, min(xs) - pad), min(img.width, max(xs) + pad)
    y1, y2 = max(0, min(ys) - pad), min(img.height, max(ys) + pad)
    if x2 <= x1 or y2 <= y1:
        return img
    return img.crop((x1, y1, x2, y2))


class HybridOCREngine:
    """
    Strategy (galgame-oriented):
    1. Whole-image manga-ocr on polarity variants (best for single-line JP dialog)
    2. PP-OCR det → manga-ocr each line (multi-line)
    3. PP-OCR full recognition fallback
    Pick the highest-scoring non-empty result.
    """

    name = "auto"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._rapid = RapidOCR(
            text_score=0.35,
            det_thresh=0.15,
            det_box_thresh=0.35,
            det_unclip_ratio=2.0,
        )
        self._manga = None
        try:
            from manga_ocr import MangaOcr

            self._manga = MangaOcr()
        except Exception:
            self._manga = None

    @property
    def backend_label(self) -> str:
        if self._manga is not None:
            return "Hybrid (manga-ocr + PP-OCR)"
        return "Hybrid (PP-OCR ONNX)"

    def recognize(self, image: Image.Image) -> str:
        raw = image.convert("RGB")
        candidates: list[str] = []

        # --- 1) whole-image manga (strong for JP dialog strips) ---
        if self._manga is not None:
            for force_invert in (None, False, True):
                variant = preprocess_for_ocr(raw, force_invert=force_invert)
                try:
                    t = _clean(self._manga(variant.convert("RGB")))
                except Exception:
                    t = ""
                if t:
                    candidates.append(t)

        # --- 2) line detection + per-line manga / rapid ---
        det_img = preprocess_for_ocr(raw, force_invert=None)
        result, _ = self._rapid(np.asarray(det_img))
        if result:
            items = sorted(result, key=lambda it: _box_order_key(it[0]))
            line_texts: list[str] = []
            for item in items:
                if not item or len(item) < 2:
                    continue
                box, rec_text = item[0], item[1]
                crop = _crop_box(det_img, box)
                crop_raw = _crop_box(raw, box)
                best_line = _clean(str(rec_text))
                if self._manga is not None:
                    for c in (
                        crop,
                        crop_raw,
                        preprocess_for_ocr(crop_raw, force_invert=True),
                    ):
                        try:
                            t = _clean(self._manga(c.convert("RGB")))
                        except Exception:
                            t = ""
                        if _score(t) > _score(best_line):
                            best_line = t
                if best_line:
                    line_texts.append(best_line)
            if line_texts:
                # dedupe consecutive
                out: list[str] = []
                for ln in line_texts:
                    if not out or out[-1] != ln:
                        out.append(ln)
                candidates.append("\n".join(out).strip())

            # rapid joined text as extra candidate
            rapid_lines = [_clean(str(it[1])) for it in items if it and len(it) > 1]
            rapid_lines = [t for t in rapid_lines if t]
            if rapid_lines:
                candidates.append("\n".join(rapid_lines).strip())

        # --- 3) pick best ---
        best = ""
        best_s = -1.0
        for c in candidates:
            s = _score(c)
            if s > best_s:
                best_s = s
                best = c
        return best
