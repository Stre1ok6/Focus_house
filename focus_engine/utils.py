from __future__ import annotations

import re
import threading
import unicodedata
from collections import OrderedDict
from typing import Any


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return float(max(minimum, min(value, maximum)))


def normalize_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00a0", " ")
    normalized = normalized.replace("｜", "|")
    normalized = normalized.replace("，", ",").replace("。", ".")
    normalized = normalized.replace("：", ":").replace("；", ";")
    normalized = normalized.replace("（", "(").replace("）", ")")
    return normalize_spaces(normalized).lower()


def extract_meaningful_tokens(text: str) -> list[str]:
    if not text:
        return []
    chinese_tokens = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    english_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-+#./]{2,}", text.lower())
    numeric_tokens = re.findall(r"\b\d{2,}\b", text)
    return chinese_tokens + english_tokens + numeric_tokens


class LRUCache:
    def __init__(self, max_size: int = 128) -> None:
        self.max_size = max(1, max_size)
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)
