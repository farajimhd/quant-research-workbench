"""Bounded reuse of pure scalar-row validation; never admission authority.

Callers must select this utility through their declared execution rules.
The validator must depend only on table name/columns and scalar row contents.
Ownership, release, lease, lineage and portfolio checks remain with their owners.
"""
from collections import OrderedDict
from struct import pack
from sys import getsizeof
from threading import RLock
from types import MappingProxyType, SimpleNamespace


class ExactScalarRowValidationCache:
    def __init__(self, validator, *, max_entries=4096, max_bytes=16 * 1024 * 1024):
        if not callable(validator):
            raise TypeError('A pure row validator is required')
        if (type(max_entries) is not int or max_entries < 1
                or type(max_bytes) is not int or max_bytes < 1):
            raise ValueError('Positive exact cache bounds are required')
        self._validator = validator
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._rows = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _snapshot(table, row):
        if type(row) not in (dict, MappingProxyType):
            return None
        schema = (table.name, tuple(table.columns))
        if (type(schema[0]) is not str
                or any(type(c) is not tuple or len(c) != 2
                       or any(type(v) is not str for v in c) for c in schema[1])):
            return None
        values = dict(row)
        if any(type(k) is not str or type(v) not in (str, int, float, bool, type(None))
               for k, v in values.items()):
            return None
        entries = tuple(sorted((k, type(v).__name__, pack('!d', v) if type(v) is float else v)
                               for k, v in values.items()))
        return (schema, entries), schema, values

    @staticmethod
    def _key_bytes(key):
        # Count shared objects repeatedly: this is a conservative bound on
        # retained key storage, independent of aliases and scalar identities.
        if type(key) is tuple:
            return getsizeof(key) + sum(ExactScalarRowValidationCache._key_bytes(v) for v in key)
        return getsizeof(key)

    def validate(self, table, row):
        snapshot = self._snapshot(table, row)
        if snapshot is None:
            return self._validator(table, row)
        key, schema, values = snapshot
        with self._lock:
            if key in self._rows:
                self.hits += 1
                self._rows.move_to_end(key)
                result = None
            else:
                self.misses += 1
                result = self._validator(SimpleNamespace(name=schema[0], columns=schema[1]), values)
                if result is not None:
                    raise ValueError('Pure scalar-row validator must return None')
            after = self._snapshot(table, row)
            if after is None or after[0] != key:
                raise ValueError('Scalar row or schema changed during validation')
            size = self._key_bytes(key) if key not in self._rows else 0
            if key not in self._rows and size <= self._max_bytes:
                while self._rows and (len(self._rows) >= self._max_entries
                                      or self._bytes + size > self._max_bytes):
                    _, removed_size = self._rows.popitem(last=False)
                    self._bytes -= removed_size
                self._rows[key] = size
                self._bytes += size
            return result

    def statistics(self):
        with self._lock:
            return dict(hits=self.hits, misses=self.misses, entries=len(self._rows),
                        retained_key_bytes=self._bytes, max_entries=self._max_entries,
                        max_bytes=self._max_bytes)
