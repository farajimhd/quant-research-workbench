"""Unselected owned scalar copies; no source or order admission authority."""
from collections import OrderedDict
from dataclasses import dataclass
from sys import getsizeof
from threading import RLock
from types import MappingProxyType

from .exact_scalar_packet_validation_cache import ExactScalarPacketValidationCache


@dataclass(frozen=True, slots=True)
class _Rows:
    rows: tuple


class OwnedScalarRowSnapshots:
    """Retain complete snapshots only for copies with no exported backing alias.

    Identity locates owned storage, not caller-owned content. Strong retention
    prevents identity reuse. Eviction returns consumers to ordinary snapshots.
    Complete scalar keys retain type, float bits, cell and row ordering.
    """

    def __init__(self, *, max_entries, max_rows, max_bytes):
        if any(type(v) is not int or v <= 0 for v in (max_entries, max_rows, max_bytes)):
            raise ValueError('Explicit positive ownership bounds required')
        self._max_entries, self._max_rows, self._max_bytes = max_entries, max_rows, max_bytes
        self._entries, self._bytes, self._lock = OrderedDict(), 0, RLock()
        self._ordinary = ExactScalarPacketValidationCache(lambda packet: None,
            packet_type=_Rows, row_fields=('rows',), max_entries=max_entries,
            max_rows=max_rows, max_bytes=max_bytes)
        self.hits = self.misses = self.bypasses = 0

    def own_rows(self, rows):
        """Copy accepted scalar rows; oversized groups remain ordinary inputs."""
        if type(rows) is not tuple:
            raise ValueError('Exact ordered row tuple required')
        if len(rows) > self._max_rows:
            with self._lock:
                self.bypasses += 1
            return rows
        before = self._ordinary._snapshot(_Rows(rows))
        if before is None:
            raise ValueError('Exact scalar mapping rows required')
        owned = tuple(MappingProxyType(dict(row)) for row in rows)
        after = self._ordinary._snapshot(_Rows(rows))
        copied = self._ordinary._snapshot(_Rows(owned))
        if before != after or before != copied:
            raise ValueError('Scalar rows changed during ownership transfer')
        key = copied[0]
        size = (ExactScalarPacketValidationCache._size(key) + getsizeof(owned)
            + sum(getsizeof(row) + getsizeof(dict(row))
                + sum(getsizeof(k) + getsizeof(v) for k, v in row.items()) for row in owned))
        with self._lock:
            self.misses += 1
            if id(owned) in self._entries:
                self._entries.move_to_end(id(owned))
                return owned
            if size > self._max_bytes:
                self.bypasses += 1
                return owned
            while self._entries and (len(self._entries) >= self._max_entries
                    or self._bytes + size > self._max_bytes):
                _, (_, _, removed) = self._entries.popitem(last=False)
                self._bytes -= removed
            self._entries[id(owned)] = (owned, key, size)
            self._bytes += size
        return owned

    def snapshot(self, rows):
        with self._lock:
            value = self._entries.get(id(rows))
            if value is None or value[0] is not rows:
                return None
            self.hits += 1
            self._entries.move_to_end(id(rows))
            return value[1]

    def ordinary_snapshot(self, rows):
        key = self._ordinary._snapshot(_Rows(rows))
        return None if key is None else key[0]

    def statistics(self):
        with self._lock:
            return dict(entries=len(self._entries), retained_bytes=self._bytes,
                hits=self.hits, misses=self.misses, bypasses=self.bypasses,
                max_entries=self._max_entries, max_rows=self._max_rows, max_bytes=self._max_bytes)


class OwnedScalarPacketValidationCache(ExactScalarPacketValidationCache):
    """Pure validation cache; only explicitly owned groups skip repeated copies."""

    def __init__(self, validator, *, ownership, **bounds):
        if type(ownership) is not OwnedScalarRowSnapshots:
            raise ValueError('Exact bounded scalar ownership store required')
        super().__init__(validator, **bounds)
        if ownership._max_rows != self._max_rows:
            raise ValueError('Ownership and validation row bounds differ')
        self._ownership = ownership

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
            key = self._ownership.snapshot(rows)
            if key is None:
                key = self._ownership.ordinary_snapshot(rows)
            if key is None:
                return None
            groups.append(key)
        return tuple(groups)
