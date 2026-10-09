"""Fresh structural verification without rebuilding a normalized JSON image.

This is a content guard, not native source, journal or execution authority.
Every mutable descendant is read on every verification. There is no global
content cache and no once-per-boundary exemption. Consumers must separately
retain their source, lease, method-code and financial-frontier checks.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from math import isfinite
from struct import pack
from weakref import WeakKeyDictionary

_ISSUED = WeakKeyDictionary()
_MAX_NODES = 2_000_000
_MAX_DEPTH = 128


@dataclass(frozen=True, slots=True)
class _Node:
    kind: str
    value_type: type
    value: object


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class ManagementStructuralGuard:
    """Identity-issued immutable image; it grants no trading capability."""
    node_count: int


def capture_management_structural_guard(value):
    active = set()
    count = 0

    def capture(current, depth):
        nonlocal count
        count += 1
        if count > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError('Management structural image exceeds declared bounds')
        cls = type(current)
        if current is None or cls in (bool, int, str):
            return _Node('scalar', cls, current)
        if cls is float:
            if not isfinite(current):
                raise ValueError('Management structural image requires finite floats')
            return _Node('float', cls, pack('>d', current))
        if cls in (date, datetime):
            return _Node('iso', cls, current.isoformat())
        if cls is Decimal:
            return _Node('decimal', cls, str(current))
        if isinstance(current, Enum):
            return _Node('enum', cls, capture(current.value, depth + 1))
        identity = id(current)
        if identity in active:
            raise ValueError('Management structural image contains a cycle')
        active.add(identity)
        try:
            if is_dataclass(current) and not isinstance(current, type):
                values = tuple((field.name, capture(getattr(current, field.name), depth + 1))
                               for field in fields(current))
                return _Node('dataclass', cls, values)
            if isinstance(current, Mapping):
                values = tuple((capture(key, depth + 1), capture(current[key], depth + 1))
                               for key in sorted(current))
                return _Node('mapping', cls, values)
            if cls in (tuple, list, frozenset):
                values = sorted(current) if cls is frozenset else current
                return _Node('sequence', cls, tuple(capture(item, depth + 1) for item in values))
            raise ValueError('Unsupported management structural value: ' + cls.__name__)
        finally:
            active.remove(identity)

    root = capture(value, 0)
    guard = ManagementStructuralGuard(count)
    _ISSUED[guard] = (root, count)
    return guard


def require_management_structural_guard(guard, value):
    """Verify the entire current graph, including nested mutable descendants."""
    if type(guard) is not ManagementStructuralGuard or guard not in _ISSUED:
        raise ValueError('Unissued management structural guard')
    root, count = _ISSUED[guard]
    if type(guard.node_count) is not int or guard.node_count != count:
        raise ValueError('Management structural guard image changed')

    def matches(node, current):
        cls = type(current)
        if cls is not node.value_type:
            return False
        kind = node.kind
        if kind == 'scalar':
            return current == node.value
        if kind == 'float':
            return isfinite(current) and pack('>d', current) == node.value
        if kind == 'iso':
            return current.isoformat() == node.value
        if kind == 'decimal':
            return str(current) == node.value
        if kind == 'enum':
            return matches(node.value, current.value)
        if kind == 'dataclass':
            current_fields = fields(current)
            return (len(current_fields) == len(node.value)
                    and all(field.name == name and matches(child, getattr(current, name))
                            for field, (name, child) in zip(current_fields, node.value)))
        if kind == 'mapping':
            if len(current) != len(node.value):
                return False
            return all(matches(key_node, key) and matches(child, current[key])
                       for key, (key_node, child) in zip(sorted(current), node.value))
        if kind == 'sequence':
            if len(current) != len(node.value):
                return False
            items = sorted(current) if cls is frozenset else current
            return all(matches(child, item) for child, item in zip(node.value, items))
        return False

    try:
        valid = matches(root, value)
    except (AttributeError, TypeError, KeyError, RecursionError):
        valid = False
    if not valid:
        raise ValueError('Management structural content changed')
    return guard
