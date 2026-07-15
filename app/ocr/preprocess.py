"""Image preprocessing to improve OCR on small / low-contrast game text."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageEnhance, ImageOps


def _looks_light_on_dark(img: Image.Image) -> bool:
    """Heuristic: dialog UIs often use light text on dark semi-transparent boxes."""
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    return float(np.mean(gray)) < 110.0


def preprocess_for_ocr(
    image: Image.Image,
    *,
    min_height: int = 72,
    force_invert: bool | None = None,
) -> Image.Image:
    """
    Upscale small dialog crops and boost contrast/sharpness.
    Optionally invert light-on-dark game UI to dark-on-light (better for PP-OCR).
    """
    img = image.convert("RGB")
    w, h = img.size
    if w < 4 or h < 4:
        return img

    invert = force_invert if force_invert is not None else _looks_light_on_dark(img)
    if invert:
        img = ImageOps.invert(img)

    scale = 1.0
    if h < min_height:
        scale = max(scale, min_height / float(h))
    if w < 200:
        scale = max(scale, 200 / float(w))
    scale = min(scale, 4.0)
    if scale > 1.05:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )

    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Contrast(img).enhance(1.35)
    img = ImageEnhance.Sharpness(img).enhance(1.4)
    return img


def preprocess_variants(image: Image.Image) -> list[Image.Image]:
    """Return a few preprocessed variants for multi-pass OCR."""
    # Auto (heuristic invert), original polarity, forced invert
    return [
        preprocess_for_ocr(image, force_invert=None),
        preprocess_for_ocr(image, force_invert=False),
        preprocess_for_ocr(image, force_invert=True),
    ]
