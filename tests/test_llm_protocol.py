from app.translate.llm_translator import (
    build_chat_messages,
    build_sampling_payload,
    build_system_prompt,
    build_user_prompt,
    parse_vlm_response,
    sampling_fingerprint,
    _extract_model_ids,
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


def test_sampling_payload_omits_unset():
    empty = build_sampling_payload()
    assert empty == {}
    only_temp = build_sampling_payload(temperature=0.2)
    assert only_temp == {"temperature": 0.2}
    assert "top_p" not in only_temp
    assert "top_k" not in only_temp
    assert "reasoning_effort" not in only_temp


def test_sampling_payload_top_k_zero_omitted():
    p = build_sampling_payload(top_k=0)
    assert "top_k" not in p
    p2 = build_sampling_payload(top_k=40)
    assert p2["top_k"] == 40


def test_sampling_payload_reasoning():
    assert build_sampling_payload(reasoning_effort="") == {}
    assert build_sampling_payload(reasoning_effort="  ") == {}
    assert build_sampling_payload(reasoning_effort="high") == {"reasoning_effort": "high"}


def test_sampling_payload_anthropic_filters():
    p = build_sampling_payload(
        temperature=0.3,
        top_p=0.9,
        top_k=20,
        frequency_penalty=0.5,
        reasoning_effort="low",
        seed=42,
        protocol="anthropic",
    )
    assert p == {"temperature": 0.3, "top_p": 0.9, "top_k": 20}
    assert "frequency_penalty" not in p
    assert "reasoning_effort" not in p
    assert "seed" not in p


def test_sampling_fingerprint_changes():
    a = sampling_fingerprint(temperature=None)
    b = sampling_fingerprint(temperature=0.2)
    assert a != b


def test_parse_vlm_response():
    src, tr = parse_vlm_response("SOURCE: こんにちは\nTRANSLATION: 你好")
    assert src == "こんにちは"
    assert tr == "你好"
    src2, tr2 = parse_vlm_response("just a plain translation")
    assert src2 == ""
    assert tr2 == "just a plain translation"


def test_extract_model_ids_openai_shape():
    payload = {
        "object": "list",
        "data": [
            {"id": "grok-4", "object": "model"},
            {"id": "gpt-4o-mini", "object": "model"},
            {"id": "grok-4"},  # dup
        ],
    }
    ids = _extract_model_ids(payload)
    assert ids == ["gpt-4o-mini", "grok-4"]


def test_extract_model_ids_anthropic_shape():
    payload = {
        "data": [
            {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet"},
            {"type": "model", "id": "claude-opus-4"},
        ],
        "has_more": False,
    }
    ids = _extract_model_ids(payload)
    assert "claude-opus-4" in ids
    assert "claude-sonnet-4-5" in ids
