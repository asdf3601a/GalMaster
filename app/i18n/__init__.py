"""Lightweight UI i18n (en + zh-Hant)."""

from __future__ import annotations

import json
from pathlib import Path

_LANG = "zh-Hant"
_STRINGS: dict[str, str] = {}
_FALLBACK: dict[str, str] = {}

_DIR = Path(__file__).resolve().parent


def available_languages() -> list[tuple[str, str]]:
    """(code, native label) pairs."""
    return [
        ("zh-Hant", "繁體中文"),
        ("en", "English"),
    ]


def current_language() -> str:
    return _LANG


def _load_file(code: str) -> dict[str, str]:
    path = _DIR / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return {}


def set_language(code: str) -> None:
    global _LANG, _STRINGS, _FALLBACK
    code = (code or "zh-Hant").strip()
    if code not in ("zh-Hant", "en"):
        code = "zh-Hant"
    _LANG = code
    _FALLBACK = _load_file("en")
    _STRINGS = _load_file(code)
    if code == "en":
        _FALLBACK = _STRINGS


def tr(key: str, **kwargs: object) -> str:
    """Translate key; missing keys fall back to English then the key itself."""
    text = _STRINGS.get(key) or _FALLBACK.get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


# Load default on import
set_language("zh-Hant")
