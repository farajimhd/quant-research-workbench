"""Inactive closed VWAP entry swing, including optional support-bounce witness."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.typed_assignment_input import validate_grouped_resistance_level


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
_SWING_NUMBERS = ("lower", "upper", "price", "pivot_at", "confirmed_at",
                  "confirmed_at_ms", "prominence", "score", "selection_score",
                  "selection_minimum_score", "reversal_distance")
_SWING_STRINGS = ("unified_level_id", "level_id", "book_version", "scale")
_SWING_FIELDS = frozenset(_SWING_NUMBERS + _SWING_STRINGS + ("side", "support_bounce"))
_SUPPORT_NUMBERS = ("lower", "upper", "price", "confirmed_at_ms")
_SUPPORT_STRINGS = ("unified_level_id", "role", "book_version",
                    "input_policy", "seed_input_policy")


def _numeric_columns(prefix: str, names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(column for name in names for column in (
        (f"{prefix}{name}_float", "Nullable(Float64)"),
        (f"{prefix}{name}_int", "Nullable(Int64)")))


SWING_TABLE = TableContract(
    "trading_assignment_vwap_entry_swing_v1",
    _KEY + (("present", "Bool"), ("bounce_present", "Bool"),
            ("side_int", "Nullable(Int8)"), ("side_str", "Nullable(String)"))
    + _numeric_columns("", _SWING_NUMBERS)
    + tuple((name, "Nullable(String)") for name in _SWING_STRINGS)
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
BOUNCE_TABLE = TableContract(
    "trading_assignment_vwap_entry_swing_bounce_v1",
    _KEY + _numeric_columns("", ("pivot_at", "pivot_price", "recovered_at"))
    + _numeric_columns("support_", _SUPPORT_NUMBERS)
    + (("support_side", "Nullable(Int8)"),)
    + tuple((f"support_{name}", "Nullable(String)") for name in _SUPPORT_STRINGS)
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
TABLES = (SWING_TABLE, BOUNCE_TABLE)


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _number(value: Any, name: str) -> tuple[float | None, int | None]:
    if type(value) is float and isfinite(value):
        return value, None
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return None, value
    raise ValueError(f"VWAP swing {name} must be finite Float64 or Int64")


def _encode_numbers(source: Mapping[str, Any], names: tuple[str, ...], prefix: str = "") -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in names:
        as_float, as_int = (_number(source[name], name) if name in source else (None, None))
        values[f"{prefix}{name}_float"] = as_float
        values[f"{prefix}{name}_int"] = as_int
    return values


def _decode_numbers(row: Mapping[str, Any], names: tuple[str, ...], prefix: str = "") -> dict[str, Any]:
    values = {}
    for name in names:
        as_float, as_int = row[f"{prefix}{name}_float"], row[f"{prefix}{name}_int"]
        if as_float is not None and as_int is not None:
            raise ValueError(f"VWAP swing {name} numeric kind is ambiguous")
        if as_float is not None or as_int is not None:
            values[name] = as_float if as_float is not None else as_int
    return values


def validate_typed_entry_swing(value: Mapping[str, Any]) -> dict[str, Any]:
    """Opt-in producer check for the complete current swing/bounce shape."""
    if not isinstance(value, Mapping) or set(value) - _SWING_FIELDS or not {
            "side", "lower", "upper", "price", "pivot_at", "confirmed_at"} <= set(value):
        raise ValueError("VWAP swing has missing or unmodeled fields")
    if value["side"] not in (1, "support") or type(value["side"]) not in (int, str):
        raise ValueError("VWAP swing side is invalid")
    for name in _SWING_NUMBERS:
        if name in value:
            _number(value[name], name)
    for name in _SWING_STRINGS:
        if name in value and (type(value[name]) is not str or not value[name]):
            raise ValueError(f"VWAP swing {name} is invalid")
    if not 0 < value["lower"] <= value["price"] <= value["upper"]:
        raise ValueError("VWAP swing price geometry is invalid")
    if not 0 < value["pivot_at"] <= value["confirmed_at"]:
        raise ValueError("VWAP swing causal clocks are invalid")
    bounce = value.get("support_bounce")
    if "support_bounce" in value:
        if (not isinstance(bounce, Mapping) or set(bounce) != {
                "pivot_at", "pivot_price", "support", "recovered_at"}):
            raise ValueError("VWAP swing support bounce fields differ")
        for name in ("pivot_at", "pivot_price", "recovered_at"):
            _number(bounce[name], f"bounce {name}")
        support = validate_grouped_resistance_level(bounce["support"])
        if (bounce["pivot_at"] != value["pivot_at"]
                or bounce["pivot_price"] != value["price"]
                or bounce["recovered_at"] < bounce["pivot_at"]):
            raise ValueError("VWAP swing bounce identity or clocks differ")
        return {**value, "support_bounce": {**bounce, "support": support}}
    return dict(value)


def project_entry_swing(value: Any, *, assignment_id: str, revision: int,
                        snapshot_id: str, session: str) -> dict[str, Any]:
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP swing assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP swing snapshot identity is invalid") from exc
    swing = validate_typed_entry_swing(value) if value is not None else {}
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    side = swing.get("side")
    parent = _seal({**identity, "present": bool(swing),
                    "bounce_present": "support_bounce" in swing,
                    "side_int": side if type(side) is int else None,
                    "side_str": side if type(side) is str else None,
                    **_encode_numbers(swing, _SWING_NUMBERS),
                    **{name: swing.get(name) for name in _SWING_STRINGS}})
    bounce_row = None
    if "support_bounce" in swing:
        bounce = swing["support_bounce"]
        support = bounce["support"]
        bounce_row = _seal({**identity,
                            **_encode_numbers(bounce, ("pivot_at", "pivot_price", "recovered_at")),
                            **_encode_numbers(support, _SUPPORT_NUMBERS, "support_"),
                            "support_side": support.get("side"),
                            **{f"support_{name}": support.get(name)
                               for name in _SUPPORT_STRINGS}})
    return {"swing": parent, "bounce": bounce_row}


def restore_entry_swing(rows: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(rows, Mapping) or set(rows) != {"swing", "bounce"}:
        raise ValueError("VWAP swing rows are incomplete")
    parent, bounce_row = rows["swing"], rows["bounce"]
    if (not isinstance(parent, Mapping)
            or set(parent) != {name for name, _ in SWING_TABLE.columns}
            or (bounce_row is not None and (not isinstance(bounce_row, Mapping)
                or set(bounce_row) != {name for name, _ in BOUNCE_TABLE.columns}))
            or type(parent["present"]) is not bool
            or type(parent["bounce_present"]) is not bool
            or parent["bounce_present"] != (bounce_row is not None)):
        raise ValueError("VWAP swing row columns or presence differ")
    swing = None
    if parent["present"]:
        if (parent["side_int"] is None) == (parent["side_str"] is None):
            raise ValueError("VWAP swing side kind differs")
        swing = {"side": parent["side_int"] if parent["side_int"] is not None
                 else parent["side_str"],
                 **_decode_numbers(parent, _SWING_NUMBERS),
                 **{name: parent[name] for name in _SWING_STRINGS if parent[name] is not None}}
        if bounce_row is not None:
            support = {**_decode_numbers(bounce_row, _SUPPORT_NUMBERS, "support_"),
                       **{name: bounce_row[f"support_{name}"] for name in _SUPPORT_STRINGS
                          if bounce_row[f"support_{name}"] is not None}}
            if bounce_row["support_side"] is not None:
                support["side"] = bounce_row["support_side"]
            swing["support_bounce"] = {
                **_decode_numbers(bounce_row, ("pivot_at", "pivot_price", "recovered_at")),
                "support": support}
    expected = project_entry_swing(
        swing, assignment_id=parent["assignment_id"], revision=parent["revision"],
        snapshot_id=parent["snapshot_id"], session=parent["session"])
    if expected != rows:
        raise ValueError("VWAP swing content or identity differs")
    return swing
