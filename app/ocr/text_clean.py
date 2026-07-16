"""Shared OCR line cleanup helpers."""

from __future__ import annotations

import re

_GARBAGE_RE = re.compile(r"^[\s□■▪▫○●◦‧·\.\-_=~`|\\/]+$")


def clean_ocr_line(text: str) -> str:
    """Strip noise / box-only garbage lines from OCR output."""
    s = (text or "").strip()
    if not s:
        return ""
    if _GARBAGE_RE.match(s):
        return ""
    boxes = s.count("□") + s.count("■")
    if boxes and boxes >= max(1, len(s.replace(" ", "")) * 0.6):
        return ""
    return s
