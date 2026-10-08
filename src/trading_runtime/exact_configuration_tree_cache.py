"""Bounded pure encoding reuse; unselected and without admission authority."""
from collections import OrderedDict
import json
from sys import getsizeof
from threading import RLock
from types import MappingProxyType

from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes


class ExactConfigurationTreeCache:
    """Key by complete canonical text, returning owned immutable scalar rows.

    Consumers must separately verify source issuance and all journal semantics.
    No existing strategy selects this utility.
    """

    def __init__(self, *, max_entries, max_input_bytes, max_rows, max_bytes):
        bounds = (max_entries, max_input_bytes, max_rows, max_bytes)
        if any(type(value) is not int or value <= 0 for value in bounds):
            raise ValueError('Explicit positive encoding cache bounds required')
        self._max_entries, self._max_input_bytes, self._max_rows, self._max_bytes = bounds
        self._cache, self._bytes, self._lock = OrderedDict(), 0, RLock()
        self.hits = self.misses = self.bypasses = 0

    @staticmethod
    def _size(text, rows):
        # Count aliases repeatedly, conservatively bounding retained objects.
        return (getsizeof(text) + getsizeof(rows) + sum(
            getsizeof(row) + getsizeof(dict(row)) + sum(
                getsizeof(key) + getsizeof(value) for key, value in row.items())
            for row in rows))

    def encode(self, text):
        if type(text) is not str:
            raise ValueError('Exact canonical configuration text required')
        with self._lock:
            cached = self._cache.get(text)
            if cached is not None:
                self.hits += 1
                self._cache.move_to_end(text)
                return cached[0]
            payload = json.loads(text)
            if canonical_json(payload) != text:
                raise ValueError('Configuration text must be complete canonical JSON')
            # Always use the existing encoder, including its type/finite/budget
            # checks. Copy each mapping so no mutable encoder alias is retained.
            rows = tuple(MappingProxyType(dict(row)) for row in encode_nodes(payload))
            self.misses += 1
            if len(text.encode('utf-8')) > self._max_input_bytes or len(rows) > self._max_rows:
                self.bypasses += 1
                return rows
            size = self._size(text, rows)
            if size > self._max_bytes:
                self.bypasses += 1
                return rows
            while self._cache and (len(self._cache) >= self._max_entries or
                                  self._bytes + size > self._max_bytes):
                _, (_, removed) = self._cache.popitem(last=False)
                self._bytes -= removed
            self._cache[text] = (rows, size)
            self._bytes += size
            return rows

    def statistics(self):
        with self._lock:
            return dict(hits=self.hits, misses=self.misses, bypasses=self.bypasses,
                        entries=len(self._cache), retained_bytes=self._bytes,
                        max_entries=self._max_entries, max_input_bytes=self._max_input_bytes,
                        max_rows=self._max_rows, max_bytes=self._max_bytes)
