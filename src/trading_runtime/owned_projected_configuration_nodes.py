"""Unselected exact projected-node ownership; no source/admission capability."""
from uuid import NAMESPACE_URL, uuid5
from .fixed_structural_lot_entry_schema import COMMON, NODE, TREE_KINDS
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes
from .exact_projected_configuration_nodes import ExactProjectedConfigurationNodeCache, ProjectedConfigurationNodes
from .owned_scalar_row_snapshots import OwnedScalarRowSnapshots


class OwnedProjectedConfigurationNodeCache(ExactProjectedConfigurationNodeCache):
    def __init__(self, *, ownership, **bounds):
        if type(ownership) is not OwnedScalarRowSnapshots:
            raise ValueError('Exact bounded node ownership store required')
        super().__init__(**bounds)
        if ownership._max_rows < self._max_rows:
            raise ValueError('Ownership capacity below declared projection row bound')
        self._ownership = ownership

    def project(self, common_text, payload_texts):
        if type(common_text) is not str or type(payload_texts) is not tuple or len(payload_texts) != len(TREE_KINDS) or any((type(text) is not str for text in payload_texts)):
            raise ValueError('Exact common text and ordered complete tree texts required')
        key = (common_text, *payload_texts)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.hits += 1
                self._cache.move_to_end(key)
                return cached[0]
            common = self._decode(common_text)
            if type(common) is not dict or set(common) != {name for name, _ in COMMON if name != 'record_id'}:
                raise ValueError('Exact projected common column inventory required')
            from .fixed_structural_lot_entry_v4 import _seal
            node_sets = tuple((encode_nodes(self._decode(text)) for text in payload_texts))
            rows = tuple((_seal(NODE, dict(common, record_id=str(uuid5(NAMESPACE_URL, f"{common['root_record_id']}:{kind}:{row['node_id']}")), tree_kind=kind, **row)) for kind, nodes in zip(TREE_KINDS, node_sets) for row in nodes))
            rows = self._ownership.own_rows(rows)
            result = ProjectedConfigurationNodes(tuple((len(nodes) for nodes in node_sets)), rows)
            self.misses += 1
            if sum((len(text.encode('utf-8')) for text in key)) > self._max_input_bytes or len(rows) > self._max_rows:
                self.bypasses += 1
                return result
            size = self._size(key, result)
            if size > self._max_bytes:
                self.bypasses += 1
                return result
            while self._cache and (len(self._cache) >= self._max_entries or self._bytes + size > self._max_bytes):
                _, (_, removed) = self._cache.popitem(last=False)
                self._bytes -= removed
            self._cache[key] = (result, size)
            self._bytes += size
            return result
