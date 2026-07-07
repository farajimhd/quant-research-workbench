from __future__ import annotations

import threading
import time


class SecRateLimiter:
    """Process-local SEC request limiter.

    SEC asks automated clients to stay under 10 requests per second. This limiter
    spaces request starts across all callers in the process.
    """

    def __init__(self, min_interval_seconds: float = 0.12) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._cooldown_until = 0.0
        self._cooldown_reason = ""

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                delay = max(self._cooldown_until, self._next_allowed) - now
                if delay <= 0:
                    self._next_allowed = now + self.min_interval_seconds
                    return
            time.sleep(min(delay, 1.0))

    def cooldown(self, seconds: float, *, reason: str) -> None:
        seconds = max(0.0, float(seconds))
        if seconds <= 0:
            return
        with self._lock:
            until = time.monotonic() + seconds
            if until > self._cooldown_until:
                self._cooldown_until = until
                self._cooldown_reason = reason

    def cooldown_status(self) -> tuple[float, str]:
        with self._lock:
            remaining = max(0.0, self._cooldown_until - time.monotonic())
            reason = self._cooldown_reason if remaining > 0 else ""
            return remaining, reason
