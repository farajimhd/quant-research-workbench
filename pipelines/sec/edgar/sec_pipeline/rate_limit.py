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

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next_allowed - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval_seconds
