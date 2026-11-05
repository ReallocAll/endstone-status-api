from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Encoded:
    body: bytes
    etag: str
    content_type: str = "application/json; charset=utf-8"


def encode(payload: Any, content_type: str = "application/json; charset=utf-8") -> Encoded:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
    return Encoded(body, etag, content_type)


class APIState:
    """Immutable HTTP snapshots; never expose Endstone objects to request threads."""

    def __init__(self, chat_capacity: int) -> None:
        self._lock = threading.Lock()
        self._cached: dict[str, Encoded] = {}
        self._messages: deque[dict[str, str]] = deque(maxlen=chat_capacity)
        self._updated_at: float | None = None

    def publish(self, responses: dict[str, Any]) -> None:
        encoded = {path: encode(body) for path, body in responses.items()}
        with self._lock:
            self._cached = encoded
            self._updated_at = time.monotonic()

    def is_fresh(self, max_age_seconds: float) -> bool:
        with self._lock:
            return self._updated_at is not None and time.monotonic() - self._updated_at <= max_age_seconds

    def get(self, path: str) -> Encoded | None:
        with self._lock:
            return self._cached.get(path)

    def add_chat(self, entry: dict[str, str]) -> None:
        with self._lock:
            self._messages.append(entry)

    def chat(self, limit: int) -> Encoded:
        with self._lock:
            messages = list(self._messages)[-limit:] if limit else []
        return encode({"api_version": "v1", "limit": limit, "messages": messages})
