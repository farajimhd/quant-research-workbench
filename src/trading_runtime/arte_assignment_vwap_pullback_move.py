"""Inactive closed VWAP entry pullback-impulse evidence.

Only ``vwap_ladder_entry.pullback_move`` is represented; the containing entry
dictionary remains outside typed assignment-state admission.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_REQUIRED = frozenset({"id", "base", "base_at", "peak", "peak_at", "retracement"})
_OPTIONAL = frozenset({"corrected", "invalid"})
TABLE = TableContract(
    "trading_assignment_vwap_entry_pullback_move_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"),
     ("present", "Bool"), ("move_id", "Nullable(Float64)"),
     ("base_float", "Nullable(Float64)"), ("base_int", "Nullable(Int64)"),
     ("base_at", "Nullable(Float64)"),
     ("peak_float", "Nullable(Float64)"), ("peak_int", "Nullable(Int64)"),
     ("peak_at", "Nullable(Float64)"),
     ("retracement", "Nullable(Float64)"),
     ("corrected", "Nullable(Bool)"), ("invalid", "Nullable(Bool)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _number(value: Any, name: str) -> tuple[float | None, int | None]:
    if type(value) is float and isfinite(value):
        return value, None
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return None, value
    raise ValueError(f"VWAP pullback {name} is not finite Float64 or Int64")


def validate_entry_pullback_move(value: Mapping[str, Any]) -> dict[str, Any]:
    """Typed-mode-only producer boundary for one closed impulse move."""
    if (not isinstance(value, Mapping) or _REQUIRED - set(value)
            or set(value) - _REQUIRED - _OPTIONAL):
        raise ValueError("VWAP pullback move has missing or unmodeled fields")
    for name in ("id", "base_at", "peak_at", "retracement"):
        if type(value[name]) is not float or not isfinite(value[name]):
            raise ValueError(f"VWAP pullback {name} must be finite Float64")
    for name in ("base", "peak"):
        _number(value[name], name)
    for name in _OPTIONAL & set(value):
        if type(value[name]) is not bool:
            raise ValueError(f"VWAP pullback {name} must be Bool")
    if (not 0 < value["base"] < value["peak"]
            or value["base_at"] > value["peak_at"]
            or not 0.2 - 1e-12 <= value["retracement"] <= 0.45 + 1e-12):
        raise ValueError("VWAP pullback move geometry differs")
    return dict(value)


def project_entry_pullback_move(value: Any, *, assignment_id: str,
                                revision: int, snapshot_id: str,
                                session: str) -> dict[str, Any]:
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP pullback assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP pullback snapshot identity is invalid") from exc
    move = validate_entry_pullback_move(value) if value is not None else {}
    base_float, base_int = (_number(move["base"], "base") if move else (None, None))
    peak_float, peak_int = (_number(move["peak"], "peak") if move else (None, None))
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session, present=bool(move),
               move_id=move.get("id"), base_float=base_float, base_int=base_int,
               base_at=move.get("base_at"), peak_float=peak_float,
               peak_int=peak_int, peak_at=move.get("peak_at"),
               retracement=move.get("retracement"),
               corrected=move.get("corrected"), invalid=move.get("invalid"))
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def restore_entry_pullback_move(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("VWAP pullback columns differ")
    if type(row["present"]) is not bool:
        raise ValueError("VWAP pullback presence differs")
    move = None
    if row["present"]:
        for name in ("base", "peak"):
            if (row[f"{name}_float"] is None) == (row[f"{name}_int"] is None):
                raise ValueError(f"VWAP pullback {name} numeric kind differs")
        move = dict(id=row["move_id"],
                    base=(row["base_float"] if row["base_float"] is not None
                          else row["base_int"]), base_at=row["base_at"],
                    peak=(row["peak_float"] if row["peak_float"] is not None
                          else row["peak_int"]), peak_at=row["peak_at"],
                    retracement=row["retracement"])
        for name in ("corrected", "invalid"):
            if row[name] is not None:
                move[name] = row[name]
    expected = project_entry_pullback_move(
        move, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if expected != row:
        raise ValueError("VWAP pullback content or identity differs")
    return move
