"""LLM provider presets (OpenAI-compatible & Anthropic-compatible)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    protocol: str  # "openai" | "anthropic"
    base_url: str
    model: str
    env_keys: tuple[str, ...]
    hint: str = ""


# Labels encode API style so UI needs only one dropdown (no separate 協議).
PROVIDER_PRESETS: list[ProviderPreset] = [
    ProviderPreset(
        id="xai",
        label="SpaceXAI / xAI",
        protocol="openai",
        base_url="https://api.x.ai/v1",
        model="grok-4-1-fast-non-reasoning",
        env_keys=("XAI_API_KEY", "OPENAI_API_KEY"),
        hint="api.x.ai",
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI 官方",
        protocol="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        env_keys=("OPENAI_API_KEY",),
        hint="api.openai.com",
    ),
    ProviderPreset(
        id="openai_compat",
        label="OpenAI 相容（自訂 / Ollama…）",
        protocol="openai",
        base_url="http://127.0.0.1:11434/v1",
        model="llama3.2",
        env_keys=("OPENAI_API_KEY", "LLM_API_KEY", "API_KEY"),
        hint="Ollama · LM Studio · OpenRouter · OneAPI · vLLM",
    ),
    ProviderPreset(
        id="anthropic",
        label="Anthropic 官方",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4-5",
        env_keys=("ANTHROPIC_API_KEY",),
        hint="api.anthropic.com",
    ),
    ProviderPreset(
        id="anthropic_compat",
        label="Anthropic 相容（自訂代理）",
        protocol="anthropic",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4-5",
        env_keys=("ANTHROPIC_API_KEY", "LLM_API_KEY", "API_KEY"),
        hint="Claude 中轉 / 相容 /v1/messages",
    ),
]


def get_preset(provider_id: str) -> ProviderPreset | None:
    for p in PROVIDER_PRESETS:
        if p.id == provider_id:
            return p
    return None


def preset_by_index(index: int) -> ProviderPreset | None:
    if 0 <= index < len(PROVIDER_PRESETS):
        return PROVIDER_PRESETS[index]
    return None
