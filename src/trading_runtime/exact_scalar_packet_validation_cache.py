"""Bounded pure packet validation reuse; never an admission capability.

The consuming immutable release must explicitly select reuse. This utility
does not install a rule or modify any existing consumer. It snapshots all
scalar rows, retaining float bits and row ordering rather than hash equality.
"""
from collections import OrderedDict
from dataclasses import fields, is_dataclass
from struct import pack
from sys import getsizeof
from threading import RLock
from types import MappingProxyType


class ExactScalarPacketValidationCache:
    def __init__(self, validator, *, packet_type, row_fields,
                 max_entries, max_rows, max_bytes):
        if not callable(validator) or not isinstance(packet_type, type):
            raise ValueError('Exact packet type and pure validator required')
        if (type(row_fields) is not tuple or not row_fields or
                any(type(name) is not str or not name for name in row_fields) or
                len(set(row_fields)) != len(row_fields)):
            raise ValueError('Ordered unique scalar-row fields required')
        if (not is_dataclass(packet_type) or not packet_type.__dataclass_params__.frozen or
                row_fields != tuple(field.name for field in fields(packet_type))):
            raise ValueError('Every frozen packet field must participate in validation reuse')
        if any(type(v) is not int or v <= 0 for v in (max_entries, max_rows, max_bytes)):
            raise ValueError('Explicit positive packet cache bounds required')
        self._validator, self._packet_type, self._fields = validator, packet_type, row_fields
        self._max_entries, self._max_rows, self._max_bytes = max_entries, max_rows, max_bytes
        self._cache, self._bytes, self._lock = OrderedDict(), 0, RLock()
        self.hits = self.misses = self.bypasses = 0

    def _snapshot(self, packet):
        if type(packet) is not self._packet_type:
            return None
        groups, count = [], 0
        for name in self._fields:
            rows = getattr(packet, name, None)
            if type(rows) is MappingProxyType:
                rows = (rows,)
            if type(rows) is not tuple:
                return None
            count += len(rows)
            if count > self._max_rows:
                return None
            group = []
            for row in rows:
                if type(row) is not MappingProxyType:
                    return None
                items = tuple(row.items())
                if any(type(k) is not str or type(v) not in
                       (str, int, float, bool, type(None)) for k, v in items):
                    return None
                group.append(tuple((k, type(v), pack('!d', v) if type(v) is float else v)
                                   for k, v in sorted(items)))
            groups.append(tuple(group))
        return tuple(groups)

    @staticmethod
    def _size(value):
        # Deliberately count shared references repeatedly: retained storage is
        # conservatively bounded, independent of object interning and aliases.
        return getsizeof(value) + (sum(ExactScalarPacketValidationCache._size(v)
                                     for v in value) if type(value) is tuple else 0)

    def validate(self, packet):
        with self._lock:
            key = self._snapshot(packet)
            if key is None:
                self.bypasses += 1
                return self._validator(packet)
            hit = key in self._cache
            if hit:
                self.hits += 1
                self._cache.move_to_end(key)
            else:
                self.misses += 1
                if self._validator(packet) is not None:
                    raise ValueError('Pure packet validator must return None')
            if self._snapshot(packet) != key:
                raise ValueError('Scalar packet changed during validation')
            if not hit:
                size = self._size(key)
                if size <= self._max_bytes:
                    while self._cache and (len(self._cache) >= self._max_entries or
                                          self._bytes + size > self._max_bytes):
                        _, removed = self._cache.popitem(last=False)
                        self._bytes -= removed
                    self._cache[key] = size
                    self._bytes += size
            return None

    def statistics(self):
        with self._lock:
            return dict(hits=self.hits, misses=self.misses, bypasses=self.bypasses,
                        entries=len(self._cache), retained_key_bytes=self._bytes,
                        max_entries=self._max_entries, max_rows=self._max_rows,
                        max_bytes=self._max_bytes)
