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

    def add(self, *, ticker: str, timestamp_us: int, payload: T, retain_ids: Iterable[int] = ()) -> int:
        item_id = int(self.next_id)
        self.next_id += 1
        self.items[item_id] = CacheItem(item_id=item_id, ticker=str(ticker), timestamp_us=int(timestamp_us), payload=payload)
        self.insertion_order.append(item_id)
        self.trim(retain_ids=retain_ids)
        return item_id

    def trim(self, *, retain_ids: Iterable[int] = ()) -> None:
        retain = {int(value) for value in retain_ids}
        scanned_retained = 0
        while len(self.insertion_order) > int(self.max_items) and scanned_retained < len(self.insertion_order):
            old_id = self.insertion_order.popleft()
            if old_id in retain and old_id in self.items:
                self.insertion_order.append(old_id)
                scanned_retained += 1
                continue
            self.items.pop(old_id, None)
            scanned_retained = 0

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
    rows: np.ndarray | None = None
    count: int = 0
    next_write: int = 0
    first_ordinal: int | None = None
    last_ordinal: int | None = None
    chunk_ids_by_origin: dict[int, int] = field(default_factory=dict)
    chunk_origin_order: Deque[int] = field(default_factory=deque)
    last_origin_encoded: int | None = None

    def push_row(self, row: np.void) -> None:
        if self.rows is None:
            self.rows = np.zeros((int(self.rows_capacity),), dtype=row.dtype)
        self.rows[int(self.next_write)] = row
        if self.count < int(self.rows_capacity):
            self.count += 1
        self.next_write = (int(self.next_write) + 1) % int(self.rows_capacity)
        self.last_ordinal = int(row["ordinal"])
        oldest = self._oldest_index()
        self.first_ordinal = int(self.rows[oldest]["ordinal"]) if self.count else None

    def has_minimum_rows(self, size: int) -> bool:
        return self.rows is not None and int(self.count) >= int(size)

    def ordinal_at_offset(self, offset: int) -> int | None:
        if self.rows is None or self.count == 0 or offset < 0 or offset >= self.count:
            return None
        index = (self._oldest_index() + int(offset)) % int(self.rows_capacity)
        return int(self.rows[index]["ordinal"])

    def window_ending(self, origin_ordinal: int, size: int) -> tuple[np.ndarray, int | None] | None:
        if self.rows is None or self.count < int(size):
            return None
        position = self._position_for_ordinal(int(origin_ordinal))
        if position is None or position + 1 < int(size):
            return None
        start_offset = int(position) + 1 - int(size)
        oldest = self._oldest_index()
        offsets = np.arange(start_offset, start_offset + int(size), dtype=np.int64)
        indices = (oldest + offsets) % int(self.rows_capacity)
        window = self.rows[indices].copy()
        previous_sip_us: int | None = None
        if start_offset > 0:
            previous_index = (oldest + start_offset - 1) % int(self.rows_capacity)
            previous_sip_us = int(self.rows[previous_index]["sip_timestamp_us"])
        return window, previous_sip_us

    def _oldest_index(self) -> int:
        if self.count < int(self.rows_capacity):
            return 0
        return int(self.next_write)

    def _position_for_ordinal(self, ordinal: int) -> int | None:
        if self.rows is None or self.first_ordinal is None or self.last_ordinal is None:
            return None
        if int(ordinal) < int(self.first_ordinal) or int(ordinal) > int(self.last_ordinal):
            return None
        offset = int(ordinal) - int(self.first_ordinal)
        if offset < 0 or offset >= int(self.count):
            return None
        index = (self._oldest_index() + offset) % int(self.rows_capacity)
        if int(self.rows[index]["ordinal"]) != int(ordinal):
            return None
        return offset

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
