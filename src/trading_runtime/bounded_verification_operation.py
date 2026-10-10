"""Bounded request-local reuse engine; this module grants no journal authority.

An integrating owner must supply its independently checked immutable frontier
and exact request identities. No native path selects this engine yet. In
particular, an ordinary reader cannot use it to acquire writer ownership.
"""
from copy import deepcopy
from hashlib import sha256
import json
from threading import get_ident


def _encode(value):
    # Generic journal JSON stringifies unknown objects. That is unsuitable for
    # proof identities: different typed objects must never collapse to a string.
    def typed(item, depth=0):
        if depth > 64:
            raise ValueError('Verification scalar content exceeds nesting bound')
        kind = type(item)
        if kind in (str, int, float, bool, type(None)):
            return [kind.__name__, item]
        if kind in (tuple, list):
            return [kind.__name__, [typed(v, depth + 1) for v in item]]
        if kind is dict and all(type(k) is str for k in item):
            return ['dict', [[k, typed(item[k], depth + 1)] for k in sorted(item)]]
        raise ValueError('Verification content requires exact scalar/container types')
    return json.dumps(typed(value), separators=(',', ':'), allow_nan=False)


class BoundedVerificationOperation:
    """Retain successful scalar proofs under one unchanged owner fence.

The fence callback must recheck the integrating owner's authority, rather
than return a caller-claimed token. Request identities must include every
verification option and source binding. Values are JSON scalar structures;
mutable recovered broker/OMS objects are deliberately unsupported.
"""

    def __init__(self, *, fence, max_requests, max_bytes):
        if (not callable(fence) or type(max_requests) is not int
                or max_requests < 1 or type(max_bytes) is not int or max_bytes < 1):
            raise ValueError('Verification operation requires explicit positive bounds')
        self._fence = fence
        self._owner = get_ident()
        self._initial = deepcopy(fence())
        self._initial_json = _encode(self._initial)
        self._max_requests = max_requests
        self._max_bytes = max_bytes
        self._retained = {}
        self._bytes = 0
        self._active = set()
        self._closed = False
        self.hits = self.misses = self.declined = 0

    def _check(self):
        if self._closed or get_ident() != self._owner:
            raise RuntimeError('Verification operation is closed or belongs to another thread')
        try:
            current = _encode(self._fence())
        except BaseException:
            self.close()
            raise
        if current != self._initial_json:
            self.close()
            raise RuntimeError('Verification owner fence changed')

    def verify(self, identity, loader):
        self._check()
        if not callable(loader):
            raise ValueError('Verification miss requires the complete verifier')
        key = _encode(identity)
        if key in self._active:
            raise RuntimeError('Verification request recursively depends on itself')
        retained = self._retained.get(key)
        if retained is not None:
            value, digest = retained
            try:
                encoded = _encode(value)
            except BaseException:
                self.close()
                raise
            if sha256(encoded.encode()).hexdigest() != digest:
                self.close()
                raise RuntimeError('Retained verification content changed')
            self._check()
            self.hits += 1
            return deepcopy(value)
        self.misses += 1
        self._active.add(key)
        try:
            value = loader()
            self._check()
            encoded = _encode(value)
            # Reparse validation is the responsibility of the complete loader.
            # Retention never changes the returned type or replaces that loader.
            size = len(key.encode()) + len(encoded.encode()) + 64
            if (len(self._retained) < self._max_requests
                    and self._bytes + size <= self._max_bytes):
                self._retained[key] = (deepcopy(value), sha256(encoded.encode()).hexdigest())
                self._bytes += size
            else:
                self.declined += 1
            return value
        finally:
            self._active.discard(key)

    def close(self):
        self._retained.clear()
        self._active.clear()
        self._bytes = 0
        self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *exc):
        self.close()
