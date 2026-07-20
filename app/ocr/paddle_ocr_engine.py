"""PaddleOCR backend.

Primary path: official `paddleocr` package (needs working paddlepaddle).
Fallback: PP-OCRv4 ONNX via RapidOCR (same PaddleOCR model family; no paddle DLL).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

from app.ocr.preprocess import preprocess_variants
from app.ocr.text_clean import clean_ocr_line


def _setup_paddle_dlls() -> None:
    """Windows: add paddle/libs so libpaddle.pyd can load native deps."""
    if os.name != "nt":
        return
    try:
        import paddle  # noqa: F401

        root = Path(paddle.__file__).resolve().parent
    except Exception:
        try:
            import site

            for sp in site.getsitepackages() + [site.getusersitepackages()]:
                libs = Path(sp) / "paddle" / "libs"
                if libs.is_dir():
                    _add_dll_dir(libs)
                    return
        except Exception:
            return
        return

    libs = root / "libs"
    if libs.is_dir():
        _add_dll_dir(libs)


def _add_dll_dir(libs: Path) -> None:
    libs_s = str(libs.resolve())
    os.environ["PATH"] = libs_s + os.pathsep + os.environ.get("PATH", "")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(libs_s)
        except OSError:
            pass


def _extract_native_texts(result) -> list[str]:
    """Parse paddleocr 2.x / 3.x predict/ocr outputs into lines."""
    lines: list[str] = []
    if result is None:
        return lines

    if isinstance(result, list) and result:
        first = result[0]
        if hasattr(first, "get") or isinstance(first, dict):
            for page in result:
                texts = None
                if isinstance(page, dict):
                    texts = page.get("rec_texts") or page.get("texts")
                else:
                    texts = (
                        page.get("rec_texts")
                        if hasattr(page, "get")
                        else getattr(page, "rec_texts", None)
                    )
                    if texts is None and hasattr(page, "json"):
                        j = page.json
                        if isinstance(j, dict):
                            texts = j.get("rec_texts") or j.get("res", {}).get(
                                "rec_texts"
                            )
                if texts:
                    for t in texts:
                        s = clean_ocr_line(str(t))
                        if s:
                            lines.append(s)
            if lines:
                return lines

        for page in result:
            if not page:
                continue
            for item in page:
                if not item:
                    continue
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    mid = item[1]
                    if isinstance(mid, (list, tuple)) and mid:
                        text = clean_ocr_line(str(mid[0]))
                    else:
                        text = clean_ocr_line(str(mid))
                    if text:
                        lines.append(text)
    return lines


def _extract_onnx_lines(result) -> list[str]:
    if not result:
        return []
    lines: list[str] = []
    for item in result:
        if not item or len(item) < 2:
            continue
        text = clean_ocr_line(str(item[1]))
        if text:
            lines.append(text)
    return lines


class PaddleOCREngine:
    """PaddleOCR — native if available, else PP-OCRv4 ONNX."""

    name = "paddle"

    def __init__(self, lang: str = "japan") -> None:
        self._mode = "onnx"
        self._native = None
        self._onnx = None
        self._lang = lang

        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        if self._try_init_native(lang):
            return
        self._init_onnx()

    def _try_init_native(self, lang: str) -> bool:
        # Native paddlepaddle often hard-crashes on Windows (illegal instruction /
        # bad DLL). Opt-in only via env to avoid killing the whole process.
        if os.environ.get("GALMASTER_USE_PADDLE_NATIVE", "").strip() not in (
            "1",
            "true",
            "TRUE",
            "yes",
        ):
            return False
        try:
            _setup_paddle_dlls()
            import paddle  # type: ignore  # noqa: F401
        except Exception:
            return False

        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception:
            return False

        lang_map = {
            "ja": "japan",
            "japan": "japan",
            "zh": "ch",
            "zh-Hant": "chinese_cht",
            "zh-Hans": "ch",
            "en": "en",
            "ko": "korean",
            "auto": "japan",
        }
        plang = lang_map.get(lang, lang or "japan")

        try:
            self._native = PaddleOCR(
                lang=plang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            self._mode = "native3"
            return True
        except TypeError:
            pass
        except Exception:
            return False

        try:
            self._native = PaddleOCR(
                use_angle_cls=True,
                lang=plang,
                show_log=False,
            )
            self._mode = "native2"
            return True
        except Exception:
            return False

    def _init_onnx(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._onnx = RapidOCR(
            text_score=0.4,
            det_thresh=0.2,
            det_box_thresh=0.4,
            det_unclip_ratio=1.9,
        )
        self._mode = "onnx"

    @property
    def backend_label(self) -> str:
        if self._mode.startswith("native"):
            return "PaddleOCR (native)"
        return "PaddleOCR (PP-OCRv4 ONNX)"

    def recognize(self, image: Image.Image) -> str:
        best = ""
        best_score = -1

        for variant in preprocess_variants(image):
            arr = np.asarray(variant.convert("RGB"))
            text = self._recognize_array(arr)
            if not text:
                continue
            # Prefer longer non-garbage text
            score = len(text.replace(" ", "").replace("\n", ""))
            if score > best_score:
                best_score = score
                best = text
            if best_score >= 4:
                # Good enough early exit
                break
        return best

    def _recognize_array(self, arr: np.ndarray) -> str:
        if self._native is not None:
            try:
                if self._mode == "native3":
                    result = self._native.predict(arr)
                else:
                    result = self._native.ocr(arr, cls=True)
                lines = _extract_native_texts(result)
                if lines:
                    return "\n".join(lines).strip()
            except Exception:
                if self._onnx is None:
                    self._init_onnx()

        if self._onnx is None:
            self._init_onnx()
        assert self._onnx is not None
        result, _ = self._onnx(arr)
        lines = _extract_onnx_lines(result)
        return "\n".join(lines).strip()
