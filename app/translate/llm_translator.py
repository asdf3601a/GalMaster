"""LLM translation via OpenAI-compatible or Anthropic-compatible APIs."""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image

from app.config import LANGUAGE_CHOICES

_LANG_NAMES = {code: name for code, name in LANGUAGE_CHOICES}

# Longest edge for VLM uploads (token / latency control)
_VLM_MAX_EDGE = 1280
_VLM_JPEG_QUALITY = 85


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


def build_vlm_system_prompt(custom_prompt: str = "") -> str:
    system = (
        "You are a professional translator for Japanese visual novels / galgames. "
        "Read dialogue and UI text from the provided screenshot and translate it. "
        "Preserve character names, honorifics tone, and line breaks when meaningful. "
        "Do not add explanations or notes beyond the required format.\n\n"
        "Respond in exactly this format (no markdown fences):\n"
        "SOURCE: <recognized source text, or leave empty if unreadable>\n"
        "TRANSLATION: <translation only>"
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


def build_vlm_user_prompt(source_lang: str, target_lang: str) -> str:
    src = _lang_label(source_lang)
    tgt = _lang_label(target_lang)
    if source_lang == "auto":
        return (
            f"Read the text in this game screenshot and translate it into {tgt}. "
            "Use the SOURCE / TRANSLATION format."
        )
    return (
        f"Read the text in this game screenshot (expected language: {src}) "
        f"and translate it into {tgt}. Use the SOURCE / TRANSLATION format."
    )


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


def build_vlm_history_messages(
    history: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """
    Prior VLM turns as SOURCE/TRANSLATION text (no OCR-style translate prompts).
    Images from prior turns are not re-sent (token cost).
    """
    messages: list[dict[str, str]] = []
    for src, tgt in history or []:
        src = (src or "").strip()
        tgt = (tgt or "").strip()
        if not src and not tgt:
            continue
        user_block = f"SOURCE: {src or '(unknown)'}\nTRANSLATION: {tgt or src or ''}"
        messages.append(
            {
                "role": "user",
                "content": (
                    "Previous dialogue line (for context only; do not re-translate):\n"
                    + user_block
                ),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": user_block,
            }
        )
    return messages


def parse_vlm_response(raw: str) -> tuple[str, str]:
    """
    Parse SOURCE:/TRANSLATION: blocks. On failure, treat whole string as translation.
    Returns (source_text, translated_text).
    """
    text = (raw or "").strip()
    if not text:
        return "", ""
    src_m = re.search(
        r"(?is)^\s*SOURCE\s*:\s*(.*?)\s*(?=^TRANSLATION\s*:)",
        text,
        re.MULTILINE,
    )
    tr_m = re.search(r"(?is)TRANSLATION\s*:\s*(.*)\s*$", text)
    if tr_m:
        source = (src_m.group(1).strip() if src_m else "")
        translated = tr_m.group(1).strip()
        return source, translated
    return "", text


def image_to_jpeg_b64(img: Image.Image, *, max_edge: int = _VLM_MAX_EDGE) -> tuple[str, str]:
    """Return (base64, media_type) for a JPEG suitable for vision APIs."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    edge = max(w, h)
    if edge > max_edge > 0:
        scale = max_edge / float(edge)
        rgb = rgb.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=_VLM_JPEG_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def build_sampling_payload(
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    frequency_penalty: float | None = None,
    presence_penalty: float | None = None,
    reasoning_effort: str | None = None,
    seed: int | None = None,
    protocol: str = "openai",
) -> dict[str, Any]:
    """
    Build optional sampling fields. Unset values are omitted entirely.
    Protocol filters keys unsupported by Anthropic Messages API.
    """
    out: dict[str, Any] = {}
    if temperature is not None:
        out["temperature"] = float(temperature)
    if top_p is not None:
        out["top_p"] = float(top_p)
    if top_k is not None and int(top_k) > 0:
        out["top_k"] = int(top_k)
    if frequency_penalty is not None:
        out["frequency_penalty"] = float(frequency_penalty)
    if presence_penalty is not None:
        out["presence_penalty"] = float(presence_penalty)
    effort = (reasoning_effort or "").strip().lower()
    if effort:
        out["reasoning_effort"] = effort
    if seed is not None:
        out["seed"] = int(seed)

    if (protocol or "openai").strip().lower() == "anthropic":
        # Anthropic Messages: temperature, top_p, top_k; drop OpenAI-only keys
        allowed = {"temperature", "top_p", "top_k"}
        out = {k: v for k, v in out.items() if k in allowed}
    return out


def sampling_fingerprint(
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    frequency_penalty: float | None = None,
    presence_penalty: float | None = None,
    reasoning_effort: str | None = None,
    seed: int | None = None,
    max_tokens: int = 2048,
) -> str:
    """Stable string for cache keys when sampling params change."""
    parts = [
        f"t={temperature}",
        f"p={top_p}",
        f"k={top_k}",
        f"fp={frequency_penalty}",
        f"pp={presence_penalty}",
        f"re={(reasoning_effort or '').strip().lower()}",
        f"s={seed}",
        f"mt={max_tokens}",
    ]
    return "|".join(parts)


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


# Chat/completions: fail fast enough that the UI does not look frozen.
DEFAULT_LLM_TIMEOUT_S = 60.0
# Model list is usually quick; keep a shorter bound.
DEFAULT_MODELS_TIMEOUT_S = 30.0


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_LLM_TIMEOUT_S,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
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
    except TimeoutError as exc:
        raise RuntimeError(f"連線逾時（{timeout:.0f}s）") from exc
    except URLError as exc:
        reason = getattr(exc, "reason", None) or exc
        # socket.timeout is often wrapped as URLError
        if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
            raise RuntimeError(f"連線逾時（{timeout:.0f}s）") from exc
        raise RuntimeError(f"連線失敗: {reason}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API 回傳非 JSON：{raw[:200]}") from exc


def _extract_model_ids(payload: Any) -> list[str]:
    """Parse OpenAI/Anthropic-style models list JSON into unique sorted ids."""
    items: list[Any]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(payload.get("models"), list):
            items = payload["models"]
        else:
            items = []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        mid = ""
        if isinstance(item, str):
            mid = item.strip()
        elif isinstance(item, dict):
            mid = str(
                item.get("id")
                or item.get("name")
                or item.get("model")
                or ""
            ).strip()
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return sorted(ids, key=str.lower)


def list_models(
    *,
    api_key: str,
    base_url: str,
    protocol: str = "openai",
    anthropic_version: str = "2023-06-01",
    timeout: float = 30.0,
) -> list[str]:
    """
    GET available model ids when the provider exposes a models list endpoint.

    - openai:  GET {base}/models  (Authorization: Bearer)
    - anthropic: GET {base}/v1/models  (x-api-key + anthropic-version)

    Raises RuntimeError/ValueError on failure (no key, HTTP error, empty parse).
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError("尚未設定 API Key")
    proto = (protocol or "openai").strip().lower()
    if proto == "anthropic":
        base = _normalize_anthropic_base(base_url)
        url = f"{base}/v1/models"
        headers = {
            "x-api-key": key,
            "anthropic-version": anthropic_version or "2023-06-01",
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }
    else:
        base = _normalize_openai_base(base_url)
        url = f"{base}/models"
        headers = {
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        }

    # Paginate when Anthropic-style has_more / last_id is present
    all_ids: list[str] = []
    seen: set[str] = set()
    next_url: str | None = url
    pages = 0
    while next_url and pages < 20:
        pages += 1
        data = _http_json("GET", next_url, headers=headers, body=None, timeout=timeout)
        for mid in _extract_model_ids(data):
            if mid not in seen:
                seen.add(mid)
                all_ids.append(mid)
        # OpenAI rarely paginates models; Anthropic may use has_more + last_id
        if isinstance(data, dict) and data.get("has_more") and data.get("last_id"):
            sep = "&" if "?" in url else "?"
            next_url = f"{url}{sep}after_id={data['last_id']}"
        else:
            next_url = None

    if not all_ids:
        raise RuntimeError("模型列表為空或無法解析（此端點可能不支援 GET /models）")
    return sorted(all_ids, key=str.lower)


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
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        reasoning_effort: str = "",
        seed: int | None = None,
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
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.frequency_penalty = frequency_penalty
        self.presence_penalty = presence_penalty
        self.reasoning_effort = reasoning_effort or ""
        self.seed = seed
        if self.protocol not in ("openai", "anthropic"):
            raise ValueError(f"不支援的 API 協議: {protocol}（請用 openai 或 anthropic）")

    def _sampling(self) -> dict[str, Any]:
        return build_sampling_payload(
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            frequency_penalty=self.frequency_penalty,
            presence_penalty=self.presence_penalty,
            reasoning_effort=self.reasoning_effort,
            seed=self.seed,
            protocol=self.protocol,
        )

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

    def translate_image(
        self,
        img: Image.Image,
        source_lang: str,
        target_lang: str,
        *,
        history: list[tuple[str, str]] | None = None,
    ) -> tuple[str, str]:
        """
        Multimodal translate from screenshot. Returns (source_text, translated_text).
        History is text-only prior turns (no images re-sent).
        """
        system = build_vlm_system_prompt(self.custom_prompt)
        b64, media_type = image_to_jpeg_b64(img)
        user_text = build_vlm_user_prompt(source_lang, target_lang)

        # Text history as prior SOURCE/TRANSLATION turns, then current image message
        history_msgs = build_vlm_history_messages(history or [])

        if self.protocol == "anthropic":
            raw = self._vlm_anthropic(system, history_msgs, user_text, b64, media_type)
        else:
            raw = self._vlm_openai(system, history_msgs, user_text, b64, media_type)
        return parse_vlm_response(raw)

    def _translate_openai(self, system: str, messages: list[dict[str, str]]) -> str:
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *messages,
        ]
        sampling = self._sampling()
        # Prefer official SDK when available; pin timeout/retries so faults
        # cannot leave the pipeline (and UI busy flag) blocked for minutes.
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=_normalize_openai_base(self.base_url),
                timeout=DEFAULT_LLM_TIMEOUT_S,
                max_retries=1,
            )
            resp = client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                max_tokens=self.max_tokens,
                **sampling,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            return (content or "").strip()
        except ImportError:
            pass

        # Fallback: raw HTTP (still OpenAI-compatible)
        base = _normalize_openai_base(self.base_url)
        url = f"{base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "max_tokens": self.max_tokens,
            **sampling,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _http_json(
            "POST", url, headers=headers, body=payload, timeout=DEFAULT_LLM_TIMEOUT_S
        )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI 相容 API 無回傳 choices: {str(data)[:300]}")
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()

    def _vlm_openai(
        self,
        system: str,
        history_msgs: list[dict[str, str]],
        user_text: str,
        b64: str,
        media_type: str,
    ) -> str:
        image_url = f"data:{media_type};base64,{b64}"
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        full_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history_msgs,
            {"role": "user", "content": user_content},
        ]
        sampling = self._sampling()
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=_normalize_openai_base(self.base_url),
                timeout=DEFAULT_LLM_TIMEOUT_S,
                max_retries=1,
            )
            resp = client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                max_tokens=self.max_tokens,
                **sampling,
            )
            content = resp.choices[0].message.content if resp.choices else ""
            return (content or "").strip()
        except ImportError:
            pass

        base = _normalize_openai_base(self.base_url)
        url = f"{base}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": full_messages,
            "max_tokens": self.max_tokens,
            **sampling,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = _http_json(
            "POST", url, headers=headers, body=payload, timeout=DEFAULT_LLM_TIMEOUT_S
        )
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"OpenAI 相容 API 無回傳 choices: {str(data)[:300]}")
        msg = choices[0].get("message") or {}
        return (msg.get("content") or "").strip()

    def _translate_anthropic(self, system: str, messages: list[dict[str, str]]) -> str:
        base = _normalize_anthropic_base(self.base_url)
        url = f"{base}/v1/messages"
        sampling = self._sampling()
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            **sampling,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = _http_json(
            "POST", url, headers=headers, body=payload, timeout=DEFAULT_LLM_TIMEOUT_S
        )
        return self._parse_anthropic_content(data)

    def _vlm_anthropic(
        self,
        system: str,
        history_msgs: list[dict[str, str]],
        user_text: str,
        b64: str,
        media_type: str,
    ) -> str:
        base = _normalize_anthropic_base(self.base_url)
        url = f"{base}/v1/messages"
        user_content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            },
            {"type": "text", "text": user_text},
        ]
        messages: list[dict[str, Any]] = [*history_msgs, {"role": "user", "content": user_content}]
        sampling = self._sampling()
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            **sampling,
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = _http_json(
            "POST", url, headers=headers, body=payload, timeout=DEFAULT_LLM_TIMEOUT_S
        )
        return self._parse_anthropic_content(data)

    @staticmethod
    def _parse_anthropic_content(data: dict[str, Any]) -> str:
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
