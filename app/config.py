"""Application settings load/save (stored next to the tool)."""

from __future__ import annotations

import base64
import json
import os
import sys
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "GalMaster"
_DPAPI_PREFIX = "dpapi:"


def project_root() -> Path:
    """GalMaster install / repo root (parent of the `app` package)."""
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    """Config lives in the tool directory: <project>/config.json."""
    return project_root() / "config.json"


def legacy_appdata_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME / "config.json"


LANGUAGE_CHOICES: list[tuple[str, str]] = [
    ("ja", "日本語"),
    ("zh-Hant", "繁體中文"),
    ("zh-Hans", "简体中文"),
    ("en", "English"),
    ("ko", "한국어"),
    ("auto", "自動偵測"),
]


@dataclass
class AppConfig:
    # LLM (optional — empty api_key => OCR only)
    # provider: xai | openai | openai_compat | anthropic | anthropic_compat
    api_provider: str = "xai"
    # protocol: openai | anthropic
    api_protocol: str = "openai"
    api_key: str = ""
    base_url: str = "https://api.x.ai/v1"
    model: str = "grok-4-1-fast-non-reasoning"
    custom_prompt: str = ""
    anthropic_version: str = "2023-06-01"
    max_tokens: int = 2048
    # Sliding window: how many prior OCR/translation turns to send as LLM context.
    # 0 = current line only (no history).
    context_history_size: int = 3

    # Languages
    source_lang: str = "ja"
    target_lang: str = "zh-Hant"

    # OCR / capture region (relative to bound window client area if hwnd set)
    region_x: int = 0
    region_y: int = 0
    region_w: int = 0
    region_h: int = 0
    # Last-known absolute physical screen rect (for stale-HWND degraded capture)
    region_abs_x: int = 0
    region_abs_y: int = 0
    region_abs_w: int = 0
    region_abs_h: int = 0
    bound_hwnd: int = 0
    bound_title: str = ""

    # Overlay
    overlay_opacity: float = 0.88
    overlay_font_size: int = 16
    overlay_click_through: bool = False
    overlay_x: int = 80
    overlay_y: int = 80
    overlay_w: int = 520
    overlay_h: int = 220

    # Hotkey
    hotkey: str = "Ctrl+Shift+T"

    # Auto monitor
    auto_monitor: bool = False
    monitor_interval_ms: int = 600
    monitor_diff_threshold: float = 0.04
    monitor_cooldown_ms: int = 1200
    # Derived from monitor_stable_ms > 0 (kept for config round-trip).
    # False / stable_ms=0: OCR as soon as change exceeds threshold.
    monitor_wait_stable: bool = True
    # Quiet duration (ms) before OCR. 0 = no wait (change triggers immediately).
    monitor_stable_ms: int = 800

    # OCR engine: "oneocr" | "manga" | "rapid" | "paddle"
    ocr_engine: str = "oneocr"

    # Window geometry
    main_window_x: int = 100
    main_window_y: int = 100
    main_window_w: int = 440
    main_window_h: int = 560

    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_region(self) -> bool:
        return self.region_w > 0 and self.region_h > 0

    @property
    def has_abs_region(self) -> bool:
        return self.region_abs_w > 0 and self.region_abs_h > 0

    @property
    def has_llm(self) -> bool:
        """True when translation API is configured."""
        return bool((self.api_key or "").strip())

    def region_tuple(self) -> tuple[int, int, int, int]:
        return (self.region_x, self.region_y, self.region_w, self.region_h)

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        self.region_x, self.region_y, self.region_w, self.region_h = x, y, w, h

    def set_abs_region(self, x: int, y: int, w: int, h: int) -> None:
        self.region_abs_x = int(x)
        self.region_abs_y = int(y)
        self.region_abs_w = int(w)
        self.region_abs_h = int(h)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for key, value in data.items():
            if key in known:
                kwargs[key] = value
            else:
                extra[key] = value
        if extra:
            kwargs["extra"] = {**kwargs.get("extra", {}), **extra}
        cfg = cls(**kwargs)
        # Normalize legacy OCR engine ids and keep wait_stable in sync with ms.
        from app.ocr.base import normalize_ocr_engine

        cfg.ocr_engine = normalize_ocr_engine(cfg.ocr_engine)
        ms = int(getattr(cfg, "monitor_stable_ms", 800) or 0)
        if not bool(getattr(cfg, "monitor_wait_stable", True)):
            cfg.monitor_stable_ms = 0
            cfg.monitor_wait_stable = False
        else:
            cfg.monitor_stable_ms = max(0, ms)
            cfg.monitor_wait_stable = cfg.monitor_stable_ms > 0
        return cfg


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        root = project_root()
        load_dotenv(root / ".env")
        load_dotenv()
    except Exception:
        pass


def _dpapi_protect(plain: str) -> str | None:
    """Protect a secret with Windows DPAPI (CurrentUser). Returns None if unavailable."""
    if sys.platform != "win32" or not plain:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        raw = plain.encode("utf-8")
        buf = ctypes.create_string_buffer(raw)
        blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "GalMaster",
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return _DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _dpapi_unprotect(stored: str) -> str | None:
    """Decrypt a DPAPI-protected secret. Returns None if not DPAPI or decrypt fails."""
    if not stored or not stored.startswith(_DPAPI_PREFIX):
        return None
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        raw = base64.b64decode(stored[len(_DPAPI_PREFIX) :].encode("ascii"))
        buf = ctypes.create_string_buffer(raw)
        blob_in = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(blob_out),
        ):
            return None
        try:
            plain = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            return plain.decode("utf-8")
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def _decode_api_key(value: str) -> str:
    """Decode DPAPI-wrapped key or return plaintext legacy value."""
    if not value:
        return ""
    plain = _dpapi_unprotect(value)
    if plain is not None:
        return plain
    if value.startswith(_DPAPI_PREFIX):
        return ""  # corrupted / wrong user
    return value


def _read_json_config(path: Path) -> AppConfig | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg = AppConfig.from_dict(data)
            if cfg.api_key:
                cfg.api_key = _decode_api_key(cfg.api_key)
            return cfg
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def load_config(path: Path | None = None) -> AppConfig:
    _load_dotenv()
    cfg_path = path or default_config_path()
    cfg = _read_json_config(cfg_path)

    # One-time migrate from old %APPDATA% location if project config missing
    if cfg is None and path is None:
        legacy = legacy_appdata_config_path()
        migrated = _read_json_config(legacy)
        if migrated is not None:
            cfg = migrated
            try:
                save_config(cfg, cfg_path)
            except OSError:
                pass

    if cfg is None:
        cfg = AppConfig()

    # Env fills empty key only (does not force LLM). Prefer env over empty disk key.
    if not cfg.api_key:
        for name in (
            "XAI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "LLM_API_KEY",
            "API_KEY",
        ):
            val = os.environ.get(name)
            if val:
                cfg.api_key = val
                break
    env_url = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("XAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
    )
    if env_url:
        cfg.base_url = env_url
    env_model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("XAI_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
    )
    if env_model:
        cfg.model = env_model
    env_proto = os.environ.get("LLM_PROTOCOL")
    if env_proto in ("openai", "anthropic"):
        cfg.api_protocol = env_proto
    env_provider = os.environ.get("LLM_PROVIDER")
    if env_provider:
        cfg.api_provider = env_provider
    return cfg


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = deepcopy(cfg.to_dict())
    # Prefer DPAPI-encrypted api_key on disk; fall back to plaintext if unavailable
    key = (payload.get("api_key") or "").strip()
    if key and not key.startswith(_DPAPI_PREFIX):
        protected = _dpapi_protect(key)
        if protected:
            payload["api_key"] = protected
    cfg_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
