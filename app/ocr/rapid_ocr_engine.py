"""RapidOCR (ONNX) backend — multi-language, relatively light."""

from __future__ import annotations

import re

import numpy as np
from PIL import Image

from app.ocr.preprocess import preprocess_variants


_GARBAGE_RE = re.compile(r"^[\s□■▪▫○●◦‧·\.\-_=~`|\\/]+$")


def _clean_line(text: str) -> str:
    s = (text or "").strip()
    if not s or _GARBAGE_RE.match(s):
        return ""
    boxes = s.count("□") + s.count("■")
    if boxes and boxes >= max(1, len(s.replace(" ", "")) * 0.6):
        return ""
    return s


class RapidOCREngine:
    name = "rapid"

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._ocr = RapidOCR(
            text_score=0.4,
            det_thresh=0.2,
            det_box_thresh=0.4,
            det_unclip_ratio=1.9,
        )

    def recognize(self, image: Image.Image) -> str:
        best = ""
        best_score = -1
        for variant in preprocess_variants(image):
            arr = np.asarray(variant.convert("RGB"))
            result, _ = self._ocr(arr)
            if not result:
                continue
            lines: list[str] = []
            for item in result:
                if not item or len(item) < 2:
                    continue
                text = _clean_line(str(item[1]))
                if text:
                    lines.append(text)
            text = "\n".join(lines).strip()
            score = len(text.replace(" ", "").replace("\n", ""))
            if score > best_score:
                best_score = score
                best = text
            if best_score >= 4:
                break
        return best
