"""Application settings load/save (stored next to the tool)."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "GalMaster"


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
    # When True: wait for stable frames after change before OCR.
    # When False: trigger OCR as soon as change exceeds threshold.
    monitor_wait_stable: bool = True
    # How long the frame must stay quiet (ms) before starting OCR.
    monitor_stable_ms: int = 800

    # OCR engine: "auto" | "manga" | "paddle" | "rapid"
    ocr_engine: str = "auto"

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
    def has_llm(self) -> bool:
        """True when translation API is configured."""
        return bool((self.api_key or "").strip())

    def region_tuple(self) -> tuple[int, int, int, int]:
        return (self.region_x, self.region_y, self.region_w, self.region_h)

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        self.region_x, self.region_y, self.region_w, self.region_h = x, y, w, h

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
        return cls(**kwargs)


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        root = project_root()
        load_dotenv(root / ".env")
        load_dotenv()
    except Exception:
        pass


def _read_json_config(path: Path) -> AppConfig | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return AppConfig.from_dict(data)
    except (OSError, json.JSONDecodeError):
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

    # Env fills empty key only (does not force LLM)
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
    cfg_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
