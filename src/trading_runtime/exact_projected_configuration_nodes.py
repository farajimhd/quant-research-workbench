"""Unselected pure projection reuse, without source or admission authority."""
from collections import OrderedDict
from dataclasses import dataclass
import json
from sys import getsizeof
from threading import RLock
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from .fixed_structural_lot_entry_schema import COMMON, NODE, TREE_KINDS
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes


@dataclass(frozen=True, slots=True)
class ProjectedConfigurationNodes:
    counts: tuple[int, ...]
    rows: tuple[MappingProxyType, ...]


class ExactProjectedConfigurationNodeCache:
    """Reuse owned scalar rows using complete canonical inputs, never hashes.

    The caller still verifies the issued source, request, semantic batch and
    complete replay equality on every call. No existing strategy selects this
    utility. Bounds govern retention only; oversized valid outputs are complete.
    """

    def __init__(self, *, max_entries, max_input_bytes, max_rows, max_bytes):
        bounds = (max_entries, max_input_bytes, max_rows, max_bytes)
        if any(type(value) is not int or value <= 0 for value in bounds):
            raise ValueError('Explicit positive projection cache bounds required')
        self._max_entries, self._max_input_bytes, self._max_rows, self._max_bytes = bounds
        self._cache, self._bytes, self._lock = OrderedDict(), 0, RLock()
        self.hits = self.misses = self.bypasses = 0

    @staticmethod
    def _decode(text):
        payload = json.loads(text)
        if canonical_json(payload) != text:
            raise ValueError('Complete canonical projection text required')
        return payload

    @staticmethod
    def _size(key, result):
        # Count shared references repeatedly, conservatively bounding retention.
        return (getsizeof(key) + sum(getsizeof(text) for text in key)
                + getsizeof(result) + getsizeof(result.counts)
                + sum(getsizeof(count) for count in result.counts)
                + getsizeof(result.rows) + sum(
                    getsizeof(row) + getsizeof(dict(row)) + sum(
                        getsizeof(name) + getsizeof(value) for name, value in row.items())
                    for row in result.rows))

    def project(self, common_text, payload_texts):
        if (type(common_text) is not str or type(payload_texts) is not tuple
                or len(payload_texts) != len(TREE_KINDS)
                or any(type(text) is not str for text in payload_texts)):
            raise ValueError('Exact common text and ordered complete tree texts required')
        key = (common_text, *payload_texts)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.hits += 1
                self._cache.move_to_end(key)
                return cached[0]
            common = self._decode(common_text)
            if (type(common) is not dict
                    or set(common) != {name for name, _ in COMMON if name != 'record_id'}):
                raise ValueError('Exact projected common column inventory required')
            # Import lazily: reuse the original complete scalar/schema/hash
            # checker. Never derive a source capability from these pure rows.
            from .fixed_structural_lot_entry_v4 import _seal
            node_sets = tuple(encode_nodes(self._decode(text)) for text in payload_texts)
            rows = tuple(_seal(NODE, dict(common,
                record_id=str(uuid5(NAMESPACE_URL,
                    f'{common["root_record_id"]}:{kind}:{row["node_id"]}')),
                tree_kind=kind, **row))
                for kind, nodes in zip(TREE_KINDS, node_sets) for row in nodes)
            result = ProjectedConfigurationNodes(tuple(len(nodes) for nodes in node_sets), rows)
            self.misses += 1
            if (sum(len(text.encode('utf-8')) for text in key) > self._max_input_bytes
                    or len(rows) > self._max_rows):
                self.bypasses += 1
                return result
            size = self._size(key, result)
            if size > self._max_bytes:
                self.bypasses += 1
                return result
            while self._cache and (len(self._cache) >= self._max_entries
                                   or self._bytes + size > self._max_bytes):
                _, (_, removed) = self._cache.popitem(last=False)
                self._bytes -= removed
            self._cache[key] = (result, size)
            self._bytes += size
            return result

    def statistics(self):
        with self._lock:
            return dict(hits=self.hits, misses=self.misses, bypasses=self.bypasses,
                        entries=len(self._cache), retained_bytes=self._bytes,
                        max_entries=self._max_entries, max_input_bytes=self._max_input_bytes,
                        max_rows=self._max_rows, max_bytes=self._max_bytes)
