from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Condition, RLock
import time
from typing import Callable, Generic, TypeVar


K = TypeVar("K")
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class _CacheEntry(Generic[V]):
    expires_at: float
    revision: str
    value: V


class BoundedTtlCache(Generic[K, V]):
    """Small in-process projection cache with explicit bounds and revision keys."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        contract_revision: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if not contract_revision.strip():
            raise ValueError("contract_revision is required")
        self.max_entries = int(max_entries)
        self.ttl_seconds = float(ttl_seconds)
        self.contract_revision = contract_revision.strip()
        self._clock = clock
        self._entries: OrderedDict[K, _CacheEntry[V]] = OrderedDict()
        self._lock = RLock()
        self._evictions = 0

    def get(self, key: K, *, source_revision: str = "") -> V | None:
        revision = self._revision(source_revision)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now or entry.revision != revision:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return entry.value

    def set(self, key: K, value: V, *, source_revision: str = "") -> None:
        entry = _CacheEntry(
            expires_at=self._clock() + self.ttl_seconds,
            revision=self._revision(source_revision),
            value=value,
        )
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def metrics(self) -> dict[str, int | float | str]:
        with self._lock:
            return {
                "contract_revision": self.contract_revision,
                "entries": len(self._entries),
                "evictions": self._evictions,
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
            }

    def _revision(self, source_revision: str) -> str:
        normalized = source_revision.strip() or "unversioned-source"
        return f"{self.contract_revision}:{normalized}"


class BoundedSingleFlightTtlCache(BoundedTtlCache[K, V]):
    """Bounded TTL cache that lets only one caller load a missing key."""

    def __init__(
        self,
        *,
        max_entries: int,
        ttl_seconds: float,
        contract_revision: str,
        wait_timeout_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
            contract_revision=contract_revision,
            clock=clock,
        )
        if wait_timeout_seconds <= 0:
            raise ValueError("wait_timeout_seconds must be positive")
        self.wait_timeout_seconds = float(wait_timeout_seconds)
        self._condition = Condition(self._lock)
        self._loading: set[tuple[K, str]] = set()
        self._loads = 0
        self._coalesced = 0

    def get_or_load(
        self,
        key: K,
        loader: Callable[[], V],
        *,
        source_revision: str = "",
    ) -> V:
        revision = self._revision(source_revision)
        flight = (key, revision)
        deadline = time.monotonic() + self.wait_timeout_seconds
        while True:
            cached = self.get(key, source_revision=source_revision)
            if cached is not None:
                return cached
            with self._condition:
                if flight not in self._loading:
                    self._loading.add(flight)
                    self._loads += 1
                    break
                self._coalesced += 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"cache load timed out for {key!r}")
                self._condition.wait(timeout=remaining)

        try:
            value = loader()
            self.set(key, value, source_revision=source_revision)
            return value
        finally:
            with self._condition:
                self._loading.discard(flight)
                self._condition.notify_all()

    def metrics(self) -> dict[str, int | float | str]:
        with self._lock:
            return {
                **super().metrics(),
                "wait_timeout_seconds": self.wait_timeout_seconds,
                "loads": self._loads,
                "coalesced": self._coalesced,
                "loading": len(self._loading),
            }
