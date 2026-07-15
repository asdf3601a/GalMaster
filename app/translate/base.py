"""Translator protocol."""

from __future__ import annotations

from typing import Protocol


class Translator(Protocol):
    def translate(self, text: str, source_lang: str, target_lang: str) -> str: ...
