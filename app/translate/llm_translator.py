"""LLM translation via OpenAI-compatible or Anthropic-compatible APIs."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import LANGUAGE_CHOICES

_LANG_NAMES = {code: name for code, name in LANGUAGE_CHOICES}


def _lang_label(code: str) -> str:
    return _LANG_NAMES.get(code, code)


def build_system_prompt(custom_prompt: str = "") -> str:
    system = (
        "You are a professional translator for Japanese visual novels / galgames. "
        "Translate game dialogue and UI text accurately. "
        "Preserve character names, honorifics tone, and line breaks when meaningful. "
        "Do not add explanations, notes, or quotes — output only the translation. "
        "If the source is already in the target language, return it unchanged."
    )
    custom = (custom_prompt or "").strip()
    if custom:
        system = f"{system}\n\nAdditional instructions:\n{custom}"
    return system


def build_user_prompt(text: str, source_lang: str, target_lang: str) -> str:
    src = _lang_label(source_lang)
    tgt = _lang_label(target_lang)
    if source_lang == "auto":
        return f"Detect the language and translate the following text into {tgt}:\n\n{text}"
    return f"Translate the following text from {src} into {tgt}:\n\n{text}"


def build_chat_messages(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    history: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """
    Build multi-turn chat messages for translation with an optional sliding window.

    `history` is a list of (source, translation) pairs, oldest first.
    """
    messages: list[dict[str, str]] = []
    for src, tgt in history or []:
        src = (src or "").strip()
        tgt = (tgt or "").strip()
        if not src:
            continue
        messages.append(
            {"role": "user", "content": build_user_prompt(src, source_lang, target_lang)}
        )
        if tgt:
            messages.append({"role": "assistant", "content": tgt})
        else:
            # Keep alternating roles even if a prior turn lacked translation
            messages.append({"role": "assistant", "content": src})
    messages.append(
        {
            "role": "user",
            "content": build_user_prompt(text, source_lang, target_lang),
        }
    )
    return messages


def _normalize_openai_base(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("尚未設定 Base URL")
    return url


def _normalize_anthropic_base(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("尚未設定 Base URL")
    # Accept either https://api.anthropic.com or .../v1
    if url.endswith("/v1"):
        return url[: -len("/v1")]
    return url


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float = 120.0,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(exc)
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"連線失敗: {exc.reason}") from exc
    if not raw.strip():
        return {}
    return json.loads(raw)


class LLMTranslator:
    """
    protocol:
      - openai:    POST {base}/chat/completions  (OpenAI / xAI / Ollama / OpenRouter…)
      - anthropic: POST {base}/v1/messages       (Anthropic official & compatible proxies)
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4-1-fast-non-reasoning",
        custom_prompt: str = "",
        protocol: str = "openai",
        anthropic_version: str = "2023-06-01",
        max_tokens: int = 2048,
    ) -> None:
        if not (api_key or "").strip():
            raise ValueError("尚未設定 API Key")
        self.api_key = api_key.strip()
        self.base_url = base_url
        self.model = model
        self.custom_prompt = custom_prompt
        self.protocol = (protocol or "openai").strip().lower()
        self.anthropic_version = anthropic_version
        self.max_tokens = max_tokens
        if self.protocol not in ("openai", "anthropic"):
            raise ValueError(f"不支援的 API 協議: {protocol}（請用 openai 或 anthropic）")

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        *,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        """
        Translate `text`.

        `history` is a sliding window of prior (source, translation) pairs
        (oldest first). Used as multi-turn context so names/pronouns stay consistent.
        """
        text = (text or "").strip()
        if not text:
            return ""

        system = build_system_prompt(self.custom_prompt)
        messages = build_chat_messages(
            text, source_lang, target_lang, history=history or []
        )

        if self.protocol == "anthropic":
            return self._translate_anthropic(system, messages)
        return self._translate_openai(system, messages)

    def _translate_openai(self, system: str, messages: list[dict[str, str]]) -> str:
        full_messages = [{"role": "system", "content": system}, *messages]
        # Prefer official SDK when available for better error handling / retries
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=_normalize_openai_base(self.base_url),
            )
            resp = client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                temperature=0.2,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            return (content or "").strip()
        except ImportError:
            pass

        # Fallback: raw HTTP (still OpenAI-compatible)
        base = _normalize_openai_base(self.base_url)
        url = f"{base}/chat/completions"
        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _http_json("POST", url, headers=headers, body=payload)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI 相容 API 無回傳 choices: {str(data)[:300]}")
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()

    def _translate_anthropic(self, system: str, messages: list[dict[str, str]]) -> str:
        base = _normalize_anthropic_base(self.base_url)
        url = f"{base}/v1/messages"
        # Anthropic requires alternating user/assistant; our builder already does that.
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "temperature": 0.2,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        # Some gateways also accept Authorization: Bearer
        headers["Authorization"] = f"Bearer {self.api_key}"

        data = _http_json("POST", url, headers=headers, body=payload)
        # Anthropic: content: [{type: text, text: "..."}]
        content = data.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(p for p in parts if p).strip()
        if isinstance(content, str):
            return content.strip()
        # Some proxies wrap OpenAI-like shape
        choices = data.get("choices")
        if choices:
            msg = choices[0].get("message") or {}
            return (msg.get("content") or "").strip()
        raise RuntimeError(f"Anthropic 相容 API 無法解析回應: {str(data)[:300]}")
