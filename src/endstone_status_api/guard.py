from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

@dataclass(slots=True)
class _ClientState:
    tokens: float
    last_refill: float
    active: int = 0
    violations: int = 0
    blocked_until: float = 0.0
    last_seen: float = 0.0


class ClientGuard:
    """Bounded per-IP concurrency and token-bucket protection."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        per_ip_connections: int,
        temporary_block_seconds: int,
        max_tracked_clients: int,
    ) -> None:
        self.rate_per_second = rate_per_second
        self.burst = float(burst)
        self.per_ip_connections = per_ip_connections
        self.temporary_block_seconds = temporary_block_seconds
        self.max_tracked_clients = max_tracked_clients
        self._clients: OrderedDict[str, _ClientState] = OrderedDict()
        self._lock = threading.Lock()
        self._accepted = 0
        self._rejected = 0
        self._blocked = 0
        self._active = 0

    def acquire(self, client_ip: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            state = self._clients.get(client_ip)
            if state is None:
                self._make_room()
                state = _ClientState(tokens=self.burst, last_refill=now, last_seen=now)
                self._clients[client_ip] = state
            else:
                self._clients.move_to_end(client_ip)

            state.last_seen = now
            if state.blocked_until > now:
                self._rejected += 1
                self._blocked += 1
                return False, max(1, int(state.blocked_until - now + 0.999))

            elapsed = max(0.0, now - state.last_refill)
            state.tokens = min(self.burst, state.tokens + elapsed * self.rate_per_second)
            state.last_refill = now

            if state.active >= self.per_ip_connections or state.tokens < 1.0:
                state.violations += 1
                self._rejected += 1
                if state.violations >= 12:
                    state.blocked_until = now + self.temporary_block_seconds
                    state.violations = 0
                    self._blocked += 1
                    return False, self.temporary_block_seconds
                return False, 1

            state.tokens -= 1.0
            state.active += 1
            state.violations = max(0, state.violations - 1)
            self._accepted += 1
            self._active += 1
            return True, 0

    def release(self, client_ip: str) -> None:
        with self._lock:
            state = self._clients.get(client_ip)
            if state is not None and state.active > 0:
                state.active -= 1
            self._active = max(0, self._active - 1)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "accepted_requests": self._accepted,
                "rejected_requests": self._rejected,
                "temporary_blocks": self._blocked,
                "active_requests": self._active,
                "tracked_clients": len(self._clients),
            }

    def _make_room(self) -> None:
        if len(self._clients) < self.max_tracked_clients:
            return
        for key, state in list(self._clients.items()):
            if state.active == 0:
                del self._clients[key]
                return
        self._clients.popitem(last=False)

