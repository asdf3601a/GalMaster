"""
Snipping Tool OneOCR via oneocr.dll (same offline model as Win11 Text actions).

Approach based on reverse engineering documented in:
  https://github.com/b1tg/win11-oneocr
  https://b1tg.github.io/post/win11-oneocr/

We load the user's already-installed ScreenSketch files (not redistributed):
  oneocr.dll, oneocr.onemodel, onnxruntime.dll

This bypasses Windows App SDK package-identity restrictions on TextRecognizer.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_char,
    c_char_p,
    c_int32,
    c_int64,
)
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import project_root
from app.ocr.preprocess import preprocess_for_ocr

# Model crypto key (from oneocr.dll RE; wrong key → CreateOcrPipeline returns 6)
_MODEL_KEY = b'kj)TGtrK>f]b[Piow.gU+nC@s""""""4'

_REQUIRED = ("oneocr.dll", "oneocr.onemodel", "onnxruntime.dll")


class _Img(Structure):
    """Image descriptor expected by RunOcrPipeline (BGRA, CV_8UC4 layout)."""

    _fields_ = [
        ("t", c_int32),  # 3 = BGRA-like
        ("col", c_int32),
        ("row", c_int32),
        ("_unk", c_int32),
        ("step", c_int64),  # bytes per row
        ("data_ptr", c_int64),
    ]


def cache_dir() -> Path:
    return project_root() / "tools" / "oneocr"


def find_screensketch_oneocr_dir() -> Path | None:
    """Locate SnippingTool folder that contains oneocr.dll."""
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-AppxPackage *ScreenSketch* | Select-Object -ExpandProperty InstallLocation",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for line in (r.stdout or "").splitlines():
            root = Path(line.strip())
            if not root.is_dir():
                continue
            for candidate in (
                root / "SnippingTool",
                root / "SnippingTool" / "SnippingTool.Vision.Interop",
                root,
            ):
                if (candidate / "oneocr.dll").is_file() and (
                    candidate / "oneocr.onemodel"
                ).is_file():
                    return candidate
    except Exception:
        pass

    # Fallback: scan WindowsApps (may need permissions)
    wa = Path(r"C:\Program Files\WindowsApps")
    try:
        for d in sorted(wa.glob("Microsoft.ScreenSketch*"), reverse=True):
            for candidate in (
                d / "SnippingTool",
                d / "SnippingTool" / "SnippingTool.Vision.Interop",
            ):
                if (candidate / "oneocr.dll").is_file():
                    return candidate
    except Exception:
        pass
    return None


def ensure_oneocr_files(*, force: bool = False) -> Path:
    """
    Ensure oneocr runtime files exist under tools/oneocr.
    Copies from the installed ScreenSketch package when missing.
    """
    dst = cache_dir()
    dst.mkdir(parents=True, exist_ok=True)
    missing = [n for n in _REQUIRED if force or not (dst / n).is_file()]
    if not missing:
        return dst

    src = find_screensketch_oneocr_dir()
    if src is None:
        raise RuntimeError(
            "找不到 Windows 剪取工具 OneOCR 檔案。\n"
            "請確認已安裝 Microsoft 剪取工具（ScreenSketch），"
            "且內含 oneocr.dll / oneocr.onemodel。"
        )
    for name in _REQUIRED:
        s = src / name
        if not s.is_file():
            raise RuntimeError(f"剪取工具目錄缺少 {name}: {src}")
        shutil.copy2(s, dst / name)
    return dst


def oneocr_available() -> bool:
    try:
        d = cache_dir()
        if all((d / n).is_file() for n in _REQUIRED):
            return True
        return find_screensketch_oneocr_dir() is not None
    except Exception:
        return False


def _rgba_to_bgra_contiguous(image: Image.Image) -> np.ndarray:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    bgra = np.empty_like(rgba)
    bgra[:, :, 0] = rgba[:, :, 2]
    bgra[:, :, 1] = rgba[:, :, 1]
    bgra[:, :, 2] = rgba[:, :, 0]
    bgra[:, :, 3] = rgba[:, :, 3]
    return np.ascontiguousarray(bgra)


class OneOCREngine:
    """Windows 11 Snipping Tool offline OCR (oneocr.dll)."""

    name = "oneocr"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dir = ensure_oneocr_files()
        self._dll = None
        self._ctx = c_int64(0)
        self._pipeline = c_int64(0)
        self._opt = c_int64(0)
        self._init_api()

    def _init_api(self) -> None:
        # DLL search path + model relative path
        os.add_dll_directory(str(self._dir))
        ctypes.WinDLL("kernel32").SetDllDirectoryW(str(self._dir))

        # Keep previous CWD for model path "oneocr.onemodel"
        self._prev_cwd = os.getcwd()
        os.chdir(self._dir)

        dll_path = str(self._dir / "oneocr.dll")
        self._dll = ctypes.CDLL(dll_path)

        def bind(name: str, argtypes, restype=c_int64):
            f = getattr(self._dll, name)
            f.argtypes = argtypes
            f.restype = restype
            return f

        self._CreateOcrInitOptions = bind(
            "CreateOcrInitOptions", [POINTER(c_int64)]
        )
        self._OcrInitOptionsSetUseModelDelayLoad = bind(
            "OcrInitOptionsSetUseModelDelayLoad", [c_int64, c_char]
        )
        self._CreateOcrPipeline = bind(
            "CreateOcrPipeline",
            [c_char_p, c_char_p, c_int64, POINTER(c_int64)],
        )
        self._CreateOcrProcessOptions = bind(
            "CreateOcrProcessOptions", [POINTER(c_int64)]
        )
        self._OcrProcessOptionsSetMaxRecognitionLineCount = bind(
            "OcrProcessOptionsSetMaxRecognitionLineCount", [c_int64, c_int64]
        )
        self._RunOcrPipeline = bind(
            "RunOcrPipeline",
            [c_int64, POINTER(_Img), c_int64, POINTER(c_int64)],
        )
        self._GetOcrLineCount = bind(
            "GetOcrLineCount", [c_int64, POINTER(c_int64)]
        )
        self._GetOcrLine = bind(
            "GetOcrLine", [c_int64, c_int64, POINTER(c_int64)]
        )
        self._GetOcrLineContent = bind(
            "GetOcrLineContent", [c_int64, POINTER(c_int64)]
        )
        # Optional cleanup
        self._ReleaseOcrResult = bind("ReleaseOcrResult", [c_int64])
        self._ReleaseOcrProcessOptions = bind(
            "ReleaseOcrProcessOptions", [c_int64]
        )
        self._ReleaseOcrPipeline = bind("ReleaseOcrPipeline", [c_int64])
        self._ReleaseOcrInitOptions = bind("ReleaseOcrInitOptions", [c_int64])

        res = self._CreateOcrInitOptions(byref(self._ctx))
        if res != 0:
            raise RuntimeError(f"CreateOcrInitOptions failed: {res}")
        res = self._OcrInitOptionsSetUseModelDelayLoad(self._ctx, 0)
        if res != 0:
            raise RuntimeError(f"OcrInitOptionsSetUseModelDelayLoad failed: {res}")

        res = self._CreateOcrPipeline(
            b"oneocr.onemodel", _MODEL_KEY, self._ctx, byref(self._pipeline)
        )
        if res != 0:
            raise RuntimeError(
                f"CreateOcrPipeline failed: {res} "
                f"（模型金鑰/檔案不符，請更新剪取工具後重拷 tools/oneocr）"
            )

        res = self._CreateOcrProcessOptions(byref(self._opt))
        if res != 0:
            raise RuntimeError(f"CreateOcrProcessOptions failed: {res}")
        res = self._OcrProcessOptionsSetMaxRecognitionLineCount(self._opt, 1000)
        if res != 0:
            raise RuntimeError(
                f"OcrProcessOptionsSetMaxRecognitionLineCount failed: {res}"
            )

    @property
    def backend_label(self) -> str:
        return "OneOCR（剪取工具離線模型）"

    def recognize(self, image: Image.Image) -> str:
        best = ""
        for force_invert in (None, False, True):
            prepared = preprocess_for_ocr(image, force_invert=force_invert)
            text = self._recognize_once(prepared)
            if len(text) > len(best):
                best = text
        return best

    def _recognize_once(self, image: Image.Image) -> str:
        buf = _rgba_to_bgra_contiguous(image)
        # Keep buffer alive across the native call
        ig = _Img(
            3,
            int(buf.shape[1]),
            int(buf.shape[0]),
            0,
            int(buf.strides[0]),
            int(buf.ctypes.data),
        )
        with self._lock:
            instance = c_int64(0)
            res = self._RunOcrPipeline(
                self._pipeline, byref(ig), self._opt, byref(instance)
            )
            if res != 0 or not instance.value:
                return ""
            try:
                lc = c_int64(0)
                self._GetOcrLineCount(instance, byref(lc))
                lines: list[str] = []
                for i in range(int(lc.value)):
                    line = c_int64(0)
                    self._GetOcrLine(instance, i, byref(line))
                    if not line.value:
                        continue
                    content = c_int64(0)
                    self._GetOcrLineContent(line, byref(content))
                    if not content.value:
                        continue
                    s = ctypes.string_at(content.value).decode(
                        "utf-8", errors="replace"
                    ).strip()
                    if s:
                        lines.append(s)
                return "\n".join(lines).strip()
            finally:
                try:
                    self._ReleaseOcrResult(instance)
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            try:
                if self._opt.value:
                    self._ReleaseOcrProcessOptions(self._opt)
            except Exception:
                pass
            try:
                if self._pipeline.value:
                    self._ReleaseOcrPipeline(self._pipeline)
            except Exception:
                pass
            try:
                if self._ctx.value:
                    self._ReleaseOcrInitOptions(self._ctx)
            except Exception:
                pass
            self._opt = c_int64(0)
            self._pipeline = c_int64(0)
            self._ctx = c_int64(0)
            try:
                os.chdir(self._prev_cwd)
            except Exception:
                pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
