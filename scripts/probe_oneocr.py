"""Probe Snipping Tool oneocr.dll (win11-oneocr approach)."""

from __future__ import annotations

import ctypes
import os
import sys
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
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DLL_DIR = ROOT / "tools" / "oneocr"


class Img(Structure):
    _fields_ = [
        ("t", c_int32),
        ("col", c_int32),
        ("row", c_int32),
        ("_unk", c_int32),
        ("step", c_int64),
        ("data_ptr", c_int64),
    ]


# From reverse engineering of oneocr.dll (b1tg/win11-oneocr)
# Crypto key for oneocr.onemodel (magic_number check in Crypto.cpp)
MODEL_KEY = b'kj)TGtrK>f]b[Piow.gU+nC@s""""""4'


def bind(dll, name, argtypes, restype=c_int64):
    f = getattr(dll, name)
    f.argtypes = argtypes
    f.restype = restype
    return f


def main() -> int:
    if not (DLL_DIR / "oneocr.dll").is_file():
        print("missing tools/oneocr/oneocr.dll — copy from ScreenSketch first")
        return 1

    os.chdir(DLL_DIR)
    os.add_dll_directory(str(DLL_DIR))
    ctypes.WinDLL("kernel32").SetDllDirectoryW(str(DLL_DIR))

    oneocr = ctypes.CDLL(str(DLL_DIR / "oneocr.dll"))
    print("loaded", oneocr._name)

    CreateOcrInitOptions = bind(oneocr, "CreateOcrInitOptions", [POINTER(c_int64)])
    OcrInitOptionsSetUseModelDelayLoad = bind(
        oneocr, "OcrInitOptionsSetUseModelDelayLoad", [c_int64, c_char]
    )
    CreateOcrPipeline = bind(
        oneocr, "CreateOcrPipeline", [c_char_p, c_char_p, c_int64, POINTER(c_int64)]
    )
    CreateOcrProcessOptions = bind(
        oneocr, "CreateOcrProcessOptions", [POINTER(c_int64)]
    )
    OcrProcessOptionsSetMaxRecognitionLineCount = bind(
        oneocr, "OcrProcessOptionsSetMaxRecognitionLineCount", [c_int64, c_int64]
    )
    RunOcrPipeline = bind(
        oneocr, "RunOcrPipeline", [c_int64, POINTER(Img), c_int64, POINTER(c_int64)]
    )
    GetOcrLineCount = bind(oneocr, "GetOcrLineCount", [c_int64, POINTER(c_int64)])
    GetOcrLine = bind(oneocr, "GetOcrLine", [c_int64, c_int64, POINTER(c_int64)])
    GetOcrLineContent = bind(
        oneocr, "GetOcrLineContent", [c_int64, POINTER(c_int64)]
    )

    ctx = c_int64(0)
    assert CreateOcrInitOptions(byref(ctx)) == 0
    assert OcrInitOptionsSetUseModelDelayLoad(ctx, 0) == 0

    pipeline = c_int64(0)
    res = CreateOcrPipeline(b"oneocr.onemodel", MODEL_KEY, ctx, byref(pipeline))
    print("CreateOcrPipeline", res, hex(pipeline.value))
    if res != 0:
        print("model load failed")
        return 2

    opt = c_int64(0)
    assert CreateOcrProcessOptions(byref(opt)) == 0
    assert OcrProcessOptionsSetMaxRecognitionLineCount(opt, 1000) == 0

    def ocr_lines(text: str, font_path: str, size: int = 42) -> list[str]:
        img = Image.new("RGBA", (720, 140), (20, 20, 30, 255))
        d = ImageDraw.Draw(img)
        d.text(
            (20, 40),
            text,
            fill=(255, 255, 255, 255),
            font=ImageFont.truetype(font_path, size),
        )
        rgba = np.asarray(img, dtype=np.uint8)
        bgra = rgba.copy()
        bgra[:, :, 0] = rgba[:, :, 2]
        bgra[:, :, 2] = rgba[:, :, 0]
        buf = np.ascontiguousarray(bgra)
        ig = Img(
            3,
            buf.shape[1],
            buf.shape[0],
            0,
            int(buf.strides[0]),
            buf.ctypes.data,
        )
        instance = c_int64(0)
        assert RunOcrPipeline(pipeline, byref(ig), opt, byref(instance)) == 0
        lc = c_int64(0)
        GetOcrLineCount(instance, byref(lc))
        out: list[str] = []
        for i in range(int(lc.value)):
            line = c_int64(0)
            GetOcrLine(instance, i, byref(line))
            if not line.value:
                continue
            content = c_int64(0)
            GetOcrLineContent(line, byref(content))
            out.append(ctypes.string_at(content.value).decode("utf-8", "replace"))
        return out

    samples = [
        ("ZH", "你好世界", r"C:\Windows\Fonts\msjh.ttc"),
        ("JA", "こんにちは、世界。", r"C:\Windows\Fonts\msgothic.ttc"),
        ("EN", "Hello World Test 123", r"C:\Windows\Fonts\arial.ttf"),
        ("MIX", "Hello こんにちは 測試", r"C:\Windows\Fonts\msgothic.ttc"),
    ]
    for name, text, font in samples:
        lines = ocr_lines(text, font)
        print(f"{name}: in={text!r} -> {lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
