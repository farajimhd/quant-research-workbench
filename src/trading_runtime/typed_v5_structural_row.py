"""Closed, opt-in source admission for V5/V6 swing-book level rows."""
from __future__ import annotations

from math import isfinite
from typing import Any, Mapping


_BASE = frozenset({
    "unified_level_id", "price", "lower", "upper", "side", "prominence",
    "created_at_ms", "confirmed_at_ms", "lifecycle", "book_version",
    "sources", "timeframes", "scale", "selection_score", "p_norm",
    "load_contract",
})
_AREA = _BASE | frozenset({
    "member_count", "selection_members", "selection_reasons",
    "selection_minimum_score", "retained_qualified_resistance",
    "retained_qualified_support",
})
_V6 = _AREA | frozenset({"oldest_member_confirmed_at_ms", "origin_contract"})
_NUMERIC = frozenset({
    "price", "lower", "upper", "prominence", "created_at_ms",
    "confirmed_at_ms", "selection_score", "p_norm", "member_count",
    "selection_minimum_score", "oldest_member_confirmed_at_ms", "side",
})
_LISTS = frozenset({"sources", "timeframes", "selection_members", "selection_reasons"})
_BOOLS = frozenset({"retained_qualified_resistance", "retained_qualified_support"})


def validate_typed_v5_structural_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reject extra or untyped source fields before copying a row into V5 state."""
    if not isinstance(row, Mapping):
        raise ValueError("V5 structural level must be a mapping")
    version = row.get("book_version")
    allowed = (_V6 if version == "causal-swing-closing-book-6" else
               _AREA if version == "causal-swing-closing-book-5" else None)
    required = {"unified_level_id", "price", "lower", "upper", "side",
                "confirmed_at_ms", "book_version", "selection_score",
                "selection_members", "selection_reasons", "member_count"}
    if allowed is None or set(row) - allowed or required - set(row):
        raise ValueError("V5 structural level has missing or unmodeled fields")
    for key, value in row.items():
        if key in _NUMERIC:
            if value is not None and (type(value) not in (int, float) or not isfinite(value)):
                raise ValueError(f"V5 structural level {key} is not finite numeric")
        elif key in _LISTS:
            if not isinstance(value, list) or any(type(item) is not str for item in value):
                raise ValueError(f"V5 structural level {key} is not a string list")
        elif key in _BOOLS:
            if type(value) is not bool:
                raise ValueError(f"V5 structural level {key} is not bool")
        elif value is not None and type(value) is not str:
            raise ValueError(f"V5 structural level {key} is not string")
    if (type(row["unified_level_id"]) is not str or not row["unified_level_id"]
            or row["side"] != -1 or row["lower"] is None
            or row["upper"] is None or not 0 < row["lower"] <= row["upper"]
            or row["selection_score"] is None):
        raise ValueError("V5 structural resistance identity or bounds are invalid")
    return dict(row)
