from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Generic, Iterable, TypeVar

import numpy as np


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheItem(Generic[T]):
    item_id: int
    ticker: str
    timestamp_us: int
    payload: T


@dataclass(slots=True)
class StableArena(Generic[T]):
    """Stores payloads behind stable integer ids.

    Rings keep only ids; the arena owns payloads until garbage collection can
    safely remove ids older than active sample references. The prototype keeps
    a bounded recent arena, which is enough for profiling and production-style
    cache behavior.
    """

    max_items: int = 1_000_000
    next_id: int = 1
    items: dict[int, CacheItem[T]] = field(default_factory=dict)
    insertion_order: Deque[int] = field(default_factory=deque)

    def add(self, *, ticker: str, timestamp_us: int, payload: T) -> int:
        item_id = int(self.next_id)
        self.next_id += 1
        self.items[item_id] = CacheItem(item_id=item_id, ticker=str(ticker), timestamp_us=int(timestamp_us), payload=payload)
        self.insertion_order.append(item_id)
        while len(self.insertion_order) > int(self.max_items):
            old_id = self.insertion_order.popleft()
            self.items.pop(old_id, None)
        return item_id

    def get(self, item_id: int) -> CacheItem[T] | None:
        return self.items.get(int(item_id))

    def payload(self, item_id: int) -> T | None:
        item = self.get(item_id)
        return None if item is None else item.payload


@dataclass(slots=True)
class LatestIdRing:
    capacity: int
    ids: Deque[int] = field(default_factory=deque)

    def push(self, item_id: int) -> int | None:
        evicted: int | None = None
        self.ids.append(int(item_id))
        while len(self.ids) > int(self.capacity):
            evicted = self.ids.popleft()
        return evicted

    def latest(self, count: int | None = None) -> tuple[int, ...]:
        values = tuple(self.ids)
        if count is not None:
            values = values[-int(count) :]
        return values

    def padded_latest(self, count: int) -> np.ndarray:
        out = np.zeros((int(count),), dtype=np.uint64)
        values = self.latest(int(count))
        if values:
            out[-len(values) :] = np.asarray(values, dtype=np.uint64)
        return out


@dataclass(frozen=True, slots=True)
class EncodedEventChunk:
    ticker: str
    origin_ordinal: int
    origin_timestamp_us: int
    previous_sip_us: int | None
    header_uint8: np.ndarray
    events_uint8: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.header_uint8.nbytes + self.events_uint8.nbytes)


@dataclass(slots=True)
class PerTickerEventCache:
    """Per-ticker raw event and encoded chunk cache."""

    ticker: str
    rows_capacity: int
    chunk_capacity: int
    rows: Deque[np.void] = field(default_factory=deque)
    chunk_ids_by_origin: dict[int, int] = field(default_factory=dict)
    chunk_origin_order: Deque[int] = field(default_factory=deque)
    last_origin_encoded: int | None = None

    def push_row(self, row: np.void) -> None:
        self.rows.append(row.copy())
        while len(self.rows) > int(self.rows_capacity):
            self.rows.popleft()

    def rows_array(self) -> np.ndarray:
        if not self.rows:
            raise ValueError("cannot build rows array from empty event cache")
        return np.asarray(list(self.rows), dtype=self.rows[0].dtype)

    def remember_chunk(self, origin_ordinal: int, chunk_id: int) -> int | None:
        self.chunk_ids_by_origin[int(origin_ordinal)] = int(chunk_id)
        self.chunk_origin_order.append(int(origin_ordinal))
        evicted_id: int | None = None
        while len(self.chunk_origin_order) > int(self.chunk_capacity):
            old_origin = self.chunk_origin_order.popleft()
            evicted_id = self.chunk_ids_by_origin.pop(old_origin, None)
        return evicted_id

    def chunk_id(self, origin_ordinal: int) -> int | None:
        return self.chunk_ids_by_origin.get(int(origin_ordinal))


@dataclass(frozen=True, slots=True)
class ExternalContextPayload:
    """Raw trainable low-frequency payload stored once in a cache arena."""

    kind: str
    token_ids: np.ndarray | None = None
    attention_mask: np.ndarray | None = None
    category_ids: np.ndarray | None = None
    numeric_values: np.ndarray | None = None
    time_features: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        total = 0
        for value in (self.token_ids, self.attention_mask, self.category_ids, self.numeric_values, self.time_features):
            if isinstance(value, np.ndarray):
                total += int(value.nbytes)
        return total


def padded_id_matrix(rows: Iterable[tuple[int, ...]], *, width: int) -> np.ndarray:
    row_list = list(rows)
    out = np.zeros((len(row_list), int(width)), dtype=np.uint64)
    for index, ids in enumerate(row_list):
        if not ids:
            continue
        clipped = tuple(int(value) for value in ids[-int(width) :])
        out[index, -len(clipped) :] = np.asarray(clipped, dtype=np.uint64)
    return out
