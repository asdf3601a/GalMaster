from app.translate.llm_translator import (
    build_chat_messages,
    build_system_prompt,
    build_user_prompt,
    _normalize_anthropic_base,
    _normalize_openai_base,
)
from app.translate.providers import PROVIDER_PRESETS, get_preset


def test_presets_cover_both_protocols():
    protos = {p.protocol for p in PROVIDER_PRESETS}
    assert "openai" in protos
    assert "anthropic" in protos
    assert get_preset("openai_compat") is not None
    assert get_preset("anthropic_compat") is not None


def test_normalize_bases():
    assert _normalize_openai_base("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert _normalize_anthropic_base("https://api.anthropic.com") == "https://api.anthropic.com"
    assert _normalize_anthropic_base("https://api.anthropic.com/v1") == "https://api.anthropic.com"


def test_prompts():
    sys = build_system_prompt("keep names")
    assert "visual novels" in sys
    assert "keep names" in sys
    user = build_user_prompt("こんにちは", "ja", "zh-Hant")
    assert "こんにちは" in user
    assert "繁體中文" in user or "zh-Hant" in user


def test_chat_messages_sliding_window():
    history = [
        ("おはよう", "早安"),
        ("元気？", "還好嗎？"),
    ]
    msgs = build_chat_messages(
        "うん", "ja", "zh-Hant", history=history
    )
    # 2 history turns * 2 + 1 current user
    assert len(msgs) == 5
    assert msgs[0]["role"] == "user"
    assert "おはよう" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "早安"
    assert msgs[-1]["role"] == "user"
    assert "うん" in msgs[-1]["content"]


def test_chat_messages_no_history():
    msgs = build_chat_messages("hello", "en", "zh-Hant", history=[])
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
