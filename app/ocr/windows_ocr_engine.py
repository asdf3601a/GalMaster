"""
Windows built-in OCR via Windows.Media.Ocr (same stack as PowerToys Text Extractor).

Design goals (match Snipping Tool / system UX):
  - Works out of the box — use the OS user-profile OCR engine; no manual language-pack
    install step required for normal use.
  - Prefer a pack matching the app source language when it is already present.
  - Never refuse to run just because ja-JP is missing; fall back to the system engine.
  - Collapse CJK inter-glyph spaces that the API often inserts.

Note: Windows 11 Snipping Tool \"Text actions\" may additionally use Windows App SDK
AI TextRecognizer (Microsoft.Windows.AI.Imaging), which needs WASDK packaging /
bootstrap and is not exposed as a simple WinRT API to plain Python processes.
We use the inbox Media.Ocr path that is always available on Windows 10/11.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
from functools import lru_cache

from PIL import Image

from app.ocr.preprocess import preprocess_for_ocr


_LANG_CANDIDATES: dict[str, list[str]] = {
    "ja": ["ja", "ja-JP"],
    "en": ["en-US", "en-GB", "en"],
    "zh-Hant": ["zh-Hant-TW", "zh-TW", "zh-Hant-HK", "zh-Hant-MO", "zh-Hant", "zh-HK"],
    "zh-Hans": ["zh-Hans-CN", "zh-CN", "zh-Hans-SG", "zh-Hans"],
    "ko": ["ko", "ko-KR"],
    "auto": [],
}


def _run_async(coro):
    """Run a coroutine from sync code (Qt worker threads have no running loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=120)


@lru_cache(maxsize=1)
def list_windows_ocr_languages() -> list[tuple[str, str]]:
    """Return [(tag, display_name), ...] for installed OCR packs."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return []
    out: list[tuple[str, str]] = []
    try:
        langs = OcrEngine.available_recognizer_languages
        if langs is not None:
            for lang in langs:
                tag = (lang.language_tag or "").strip()
                name = (lang.display_name or tag).strip()
                if tag:
                    out.append((tag, name))
    except Exception:
        out = _probe_common_languages()
    return out


def _probe_common_languages() -> list[tuple[str, str]]:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return []
    common = [
        "ja-JP",
        "en-US",
        "en-GB",
        "zh-Hant-TW",
        "zh-TW",
        "zh-Hant-HK",
        "zh-Hans-CN",
        "zh-CN",
        "ko-KR",
    ]
    found: list[tuple[str, str]] = []
    for tag in common:
        try:
            if OcrEngine.is_language_supported(Language(tag)):
                found.append((tag, tag))
        except Exception:
            continue
    try:
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is not None:
            tag = (eng.recognizer_language.language_tag or "").strip()
            if tag and tag not in {t for t, _ in found}:
                name = (eng.recognizer_language.display_name or tag).strip()
                found.insert(0, (tag, name))
    except Exception:
        pass
    return found


def _is_supported(tag: str) -> bool:
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        return bool(OcrEngine.is_language_supported(Language(tag)))
    except Exception:
        return False


def _create_from_tag(lang_tag: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    eng = OcrEngine.try_create_from_language(Language(lang_tag))
    if eng is None:
        raise RuntimeError(f"無法建立 Windows OCR（{lang_tag}）")
    return eng


def _create_from_user_profile():
    """System default — same idea as Snipping Tool / PowerToys (no extra install)."""
    from winrt.windows.media.ocr import OcrEngine

    eng = OcrEngine.try_create_from_user_profile_languages()
    if eng is None:
        raise RuntimeError(
            "Windows OCR 無法使用系統語言引擎。"
            "請確認 Windows 版本支援 OCR（Windows 10 1803+ / Windows 11）。"
        )
    return eng


def resolve_windows_ocr_tag(preferred: str = "auto") -> str:
    """
    Resolve which language tag we will use.

    Prefers an installed pack matching `preferred` (app source_lang), otherwise
    the user-profile / first installed pack. Never requires installing a pack.
    """
    preferred = (preferred or "auto").strip()

    # Prefer explicit match among installed packs
    candidates = list(_LANG_CANDIDATES.get(preferred, []))
    if preferred not in _LANG_CANDIDATES and preferred not in ("auto", ""):
        candidates = [preferred]
    for cand in candidates:
        if _is_supported(cand):
            return cand
        cl = cand.lower()
        for tag, _ in list_windows_ocr_languages():
            if tag.lower() == cl or tag.lower().startswith(cl + "-"):
                if _is_supported(tag):
                    return tag

    # User profile (system languages already configured on this PC)
    try:
        eng = _create_from_user_profile()
        tag = (eng.recognizer_language.language_tag or "").strip()
        if tag:
            return tag
    except Exception:
        pass

    installed = list_windows_ocr_languages()
    if installed:
        return installed[0][0]

    # Last resort: still try profile create (may work even if list is empty)
    eng = _create_from_user_profile()
    return (eng.recognizer_language.language_tag or "user-profile").strip()


async def _recognize_rgba_async(engine, rgba: Image.Image) -> str:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    width, height = rgba.size
    if width < 1 or height < 1:
        return ""

    max_dim = 4000
    if max(width, height) > max_dim:
        scale = max_dim / max(width, height)
        rgba = rgba.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
        width, height = rgba.size

    writer = DataWriter()
    writer.write_bytes(bytearray(rgba.tobytes()))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(),
        BitmapPixelFormat.RGBA8,
        width,
        height,
    )
    result = await engine.recognize_async(bitmap)
    if result is None:
        return ""
    text = (result.text or "").strip()
    if text:
        return text
    lines: list[str] = []
    try:
        for line in result.lines:
            t = (line.text or "").strip()
            if t:
                lines.append(t)
    except Exception:
        pass
    return "\n".join(lines).strip()


def _normalize_windows_ocr_text(text: str) -> str:
    """Collapse spaces Windows OCR inserts between CJK glyphs."""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(
        r"(?<=[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef])\s+(?=[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef])",
        "",
        s,
    )
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _score_text(text: str, preferred: str) -> float:
    """Prefer real script content; slight boost when matching preferred language."""
    if not text:
        return -1.0
    s = text.replace("\n", "").replace(" ", "")
    if not s:
        return -1.0
    cjk = sum(
        1
        for ch in s
        if "\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff"
    )
    kana = sum(1 for ch in s if "\u3040" <= ch <= "\u30ff")
    alnum = sum(1 for ch in s if ch.isalnum() and ord(ch) < 128)
    hangul = sum(1 for ch in s if "\uac00" <= ch <= "\ud7af")
    score = float(len(s) + 2 * cjk + alnum + hangul)
    pref = (preferred or "").lower()
    if pref.startswith("ja") and kana:
        score += 8 * kana
    if pref.startswith("zh") and cjk:
        score += 2 * cjk
    if pref.startswith("en") and alnum:
        score += 2 * alnum
    if pref.startswith("ko") and hangul:
        score += 8 * hangul
    return score


class WindowsOCREngine:
    """
    Inbox Windows OCR (Windows.Media.Ocr).

    Uses the system user-profile engine by default — no extra download required.
    When additional OCR language packs are already installed, may try them and
    keep the highest-scoring result for the current source language.
    """

    name = "windows"

    def __init__(self, lang: str = "auto") -> None:
        try:
            from winrt.windows.media.ocr import OcrEngine  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Windows OCR 綁定未安裝。請執行: uv sync"
            ) from exc

        self._preferred = lang or "auto"
        self._engines: list[tuple[str, object]] = []
        self._primary_tag = ""

        # 1) Preferred pack if present
        try:
            tag = resolve_windows_ocr_tag(self._preferred)
            if tag and _is_supported(tag):
                self._engines.append((tag, _create_from_tag(tag)))
                self._primary_tag = tag
        except Exception:
            pass

        # 2) Always include user-profile engine (system default, no install)
        try:
            profile = _create_from_user_profile()
            ptag = (profile.recognizer_language.language_tag or "user-profile").strip()
            if ptag not in {t for t, _ in self._engines}:
                self._engines.append((ptag, profile))
            if not self._primary_tag:
                self._primary_tag = ptag
        except Exception:
            pass

        # 3) Any other already-installed packs (multi-lang PCs)
        for tag, _name in list_windows_ocr_languages():
            if tag in {t for t, _ in self._engines}:
                continue
            try:
                if _is_supported(tag):
                    self._engines.append((tag, _create_from_tag(tag)))
            except Exception:
                continue

        if not self._engines:
            # Final attempt — must work on a normal Win10/11 install
            eng = _create_from_user_profile()
            tag = (eng.recognizer_language.language_tag or "user-profile").strip()
            self._engines = [(tag, eng)]
            self._primary_tag = tag

    @property
    def backend_label(self) -> str:
        tags = ", ".join(t for t, _ in self._engines[:3])
        extra = f" +{len(self._engines) - 3}" if len(self._engines) > 3 else ""
        return f"Windows OCR（{tags}{extra}）"

    @property
    def language_tag(self) -> str:
        return self._primary_tag

    def recognize(self, image: Image.Image) -> str:
        best = ""
        best_score = -1.0
        # Polarity variants help light-on-dark game UI
        variants: list[Image.Image] = []
        for force_invert in (None, False, True):
            prepared = preprocess_for_ocr(image, force_invert=force_invert)
            variants.append(prepared.convert("RGBA"))

        for _tag, engine in self._engines:
            for rgba in variants:
                try:
                    text = _run_async(_recognize_rgba_async(engine, rgba))
                except Exception:
                    text = ""
                text = _normalize_windows_ocr_text(text)
                sc = _score_text(text, self._preferred)
                if sc > best_score:
                    best_score = sc
                    best = text
        return best
