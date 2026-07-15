"""In-memory translation cache keyed by text + language pair + model."""

from __future__ import annotations

import hashlib
from collections import OrderedDict


class TranslationCache:
    def __init__(self, max_size: int = 256) -> None:
        self._max_size = max_size
        self._data: OrderedDict[str, str] = OrderedDict()

    @staticmethod
    def make_key(text: str, source: str, target: str, model: str) -> str:
        raw = f"{source}|{target}|{model}|{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: str, value: str) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data
