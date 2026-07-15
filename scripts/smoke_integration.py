"""Integration smoke without full interactive UI loop."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    print(msg, flush=True)


def make_text_image(text: str = "GALMASTER") -> Image.Image:
    img = Image.new("RGB", (520, 120), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    dr.text((16, 30), text, fill=(0, 0, 0), font=font)
    return img


def main() -> int:
    from app.capture.screenshot import (
        capture_screen_region,
        image_to_gray_array,
        mean_abs_diff,
    )
    from app.capture.windows import enum_windows
    from app.config import AppConfig
    from app.hotkeys.global_hotkey import parse_hotkey
    from app.ocr.paddle_ocr_engine import PaddleOCREngine
    from app.pipeline import PipelineResult, TranslationPipeline
    from app.translate.cache import TranslationCache
    from app.ui.main_window import MainWindow
    from app.ui.overlay_window import OverlayWindow

    errors: list[str] = []

    wins = enum_windows()
    log(f"[windows] count={len(wins)}")
    if not wins:
        errors.append("enum_windows returned empty")

    try:
        shot = capture_screen_region(0, 0, 120, 80)
        assert shot.size == (120, 80)
        log(f"[capture] ok size={shot.size}")
    except Exception as exc:
        errors.append(f"capture failed: {exc}")
        log(f"[capture] FAIL {exc}")

    a = image_to_gray_array(Image.new("RGB", (50, 50), (0, 0, 0)))
    b = image_to_gray_array(Image.new("RGB", (50, 50), (255, 255, 255)))
    d = mean_abs_diff(a, b)
    assert d > 0.5
    log(f"[diff] ok d={d:.3f}")

    parse_hotkey("Ctrl+Shift+T")
    log("[hotkey] ok")

    img = make_text_image()
    ocr_eng = PaddleOCREngine(lang="en")
    text = ocr_eng.recognize(img)
    log(f"[ocr] backend={ocr_eng.backend_label} text={text!r}")
    if "GAL" not in text.upper() and "MASTER" not in text.upper():
        errors.append(f"OCR unexpected: {text!r}")
    else:
        log("[ocr] ok")

    c = TranslationCache()
    k = TranslationCache.make_key("a", "ja", "zh-Hant", "m")
    c.put(k, "甲")
    assert c.get(k) == "甲"
    log("[cache] ok")

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = AppConfig(region_w=10, region_h=10)
    main_win = MainWindow(cfg)
    main_win.show()
    overlay = OverlayWindow()
    overlay.set_content(source="原文テスト", translation="翻譯測試", status="smoke")
    overlay.show()
    overlay.set_click_through(False)
    collected = main_win.collect_config()
    assert collected.region_w == 10
    log("[ui] main+overlay ok")

    pipe = TranslationPipeline()
    results: list[PipelineResult] = []

    def on_done(r: object) -> None:
        assert isinstance(r, PipelineResult)
        results.append(r)
        log(f"[pipeline] source={r.source_text!r} err={r.error!r} tr={r.translated_text!r}")
        pipe.shutdown()
        main_win.force_close()
        overlay.close()
        app.quit()

    pipe.finished.connect(on_done)
    cfg2 = AppConfig(
        api_key="",
        region_w=1,
        region_h=1,
        source_lang="en",
        target_lang="zh-Hant",
        ocr_engine="paddle",
    )
    pipe.request(cfg2, img)
    log("[pipeline] requested")

    QTimer.singleShot(45000, app.quit)
    app.exec()

    if not results:
        errors.append("pipeline produced no result (timeout?)")
    else:
        r = results[0]
        if r.source_text:
            log("[pipeline] OCR path ok")
            if r.ocr_only:
                log("[pipeline] OCR-only (no API key) ok")
            elif r.translated_text:
                log("[pipeline] full translate ok")
            elif r.error:
                log(f"[pipeline] post-OCR error: {r.error}")
        elif r.error:
            log(f"[pipeline] error: {r.error}")
            if "OCR" in r.error:
                errors.append(r.error)

    if errors:
        log("FAILURES:")
        for e in errors:
            log(" - " + e)
        return 1
    log("ALL INTEGRATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
