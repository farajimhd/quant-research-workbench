"""Bounded pure AST summaries; callers must freshly read and verify source."""
from __future__ import annotations

import ast
from collections import OrderedDict
from hashlib import sha256
import sys
from threading import Lock
from typing import NamedTuple


class SymbolObservation(NamedTuple):
    name: str
    digests: tuple[str, ...]


class CacheInfo(NamedTuple):
    entries: int
    retained_bytes: int
    hits: int
    misses: int


def _size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    return sys.getsizeof(value) + (sum(_size(item, seen) for item in value)
                                  if isinstance(value, tuple) else 0)


class SourceAstSummaryCache:
    """Cache strings/tuples only, keyed by exact text and selection semantics.

    Parsing occurs outside the lock. Racing computations are harmless; only
    one immutable result is retained. No parsed AST or approval is retained.
    """
    def __init__(self, *, max_entries=256, max_bytes=32 * 1024 * 1024):
        if (type(max_entries) is not int or not 1 <= max_entries <= 256
                or type(max_bytes) is not int
                or not sys.getsizeof(OrderedDict()) <= max_bytes <= 32 * 1024 * 1024):
            raise ValueError("Invalid source summary cache bounds")
        self._limits = (max_entries, max_bytes)
        self._items = OrderedDict()
        self._bytes = self._hits = self._misses = 0
        self._lock = Lock()

    @property
    def max_entries(self):
        return self._limits[0]

    @property
    def max_bytes(self):
        return self._limits[1]

    def info(self):
        with self._lock:
            return CacheInfo(len(self._items), self._bytes + sys.getsizeof(self._items),
                             self._hits, self._misses)

    def _get(self, key, compute):
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                self._items.move_to_end(key)
                self._hits += 1
                return entry[0]
            self._misses += 1
        result = compute()
        size = _size((key, result)) + sys.getsizeof(0)
        # Oversized authoritative input is still computed, never truncated.
        if size + sys.getsizeof(OrderedDict()) > self.max_bytes:
            return result
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                self._items.move_to_end(key)
                return existing[0]
            self._items[key] = (result, size)
            self._bytes += size
            while (len(self._items) > self.max_entries
                   or self._bytes + sys.getsizeof(self._items) > self.max_bytes):
                _, (_, removed_size) = self._items.popitem(last=False)
                self._bytes -= removed_size
        return result

    def module_digest(self, source_text: str) -> str:
        if type(source_text) is not str:
            raise TypeError("Source text must be a string")
        return self._get(("module", source_text),
                         lambda: sha256(ast.unparse(ast.parse(source_text)).encode()).hexdigest())

    def symbol_summary(self, source_text: str, names: tuple[str, ...], *,
                       kinds=("FunctionDef", "AsyncFunctionDef", "ClassDef")):
        if (type(source_text) is not str or type(names) is not tuple
                or any(type(name) is not str for name in names)
                or type(kinds) is not tuple or not kinds
                or any(kind not in ("FunctionDef", "AsyncFunctionDef", "ClassDef") for kind in kinds)):
            raise TypeError("Invalid immutable source symbol selectors")
        def compute():
            tree = ast.parse(source_text)
            selected = {name: [] for name in names}
            # Match the original ast.walk scope/order, including nested symbols.
            for node in ast.walk(tree):
                if type(node).__name__ in kinds and getattr(node, "name", None) in selected:
                    selected[node.name].append(sha256(ast.unparse(node).encode()).hexdigest())
            if "__module__" in selected:
                selected["__module__"] = [sha256(ast.unparse(tree).encode()).hexdigest()]
            return tuple(SymbolObservation(name, tuple(selected[name])) for name in names)
        return self._get(("symbols", source_text, names, kinds), compute)


_CACHE = SourceAstSummaryCache()


def canonical_module_ast_digest(source_text: str) -> str:
    return _CACHE.module_digest(source_text)


def canonical_symbol_ast_summary(source_text: str, names: tuple[str, ...], *,
                                 kinds=("FunctionDef", "AsyncFunctionDef", "ClassDef")):
    return _CACHE.symbol_summary(source_text, names, kinds=kinds)
