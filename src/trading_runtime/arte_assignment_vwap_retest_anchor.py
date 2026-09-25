"""Inactive VWAP entry retest witness with typed level and swing children."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_assignment_pending_breakout import (
    PARENT_TABLE as PENDING_PARENT, _OPTIONAL as _ANCHOR_OPTIONAL,
)
from src.trading_runtime.arte_assignment_vwap_swing import (
    SWING_TABLE as SOURCE_SWING_TABLE, BOUNCE_TABLE as SOURCE_BOUNCE_TABLE,
    project_entry_swing, restore_entry_swing, validate_typed_entry_swing,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
_ANCHOR_COLUMNS = tuple(column for column in PENDING_PARENT.columns
                        if column[0].startswith("anchor_"))
RETEST_TABLE = TableContract(
    "trading_assignment_vwap_entry_retest_anchor_v1",
    _KEY + (("pivot_at_float", "Nullable(Float64)"),
            ("pivot_at_int", "Nullable(Int64)"),
            ("pivot_price_float", "Nullable(Float64)"),
            ("pivot_price_int", "Nullable(Int64)"),
            ("recovered_at_float", "Nullable(Float64)"),
            ("recovered_at_int", "Nullable(Int64)"),
            ("break_count", "UInt32")) + _ANCHOR_COLUMNS
    + (("member_count", "UInt16"), ("member_hash", "FixedString(64)"),
       ("swing_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
MEMBER_TABLE = TableContract(
    "trading_assignment_vwap_entry_retest_member_v1",
    _KEY + (("ordinal", "UInt16"), ("member_id", "String"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
SWING_TABLE = TableContract(
    "trading_assignment_vwap_entry_retest_swing_v1",
    SOURCE_SWING_TABLE.columns, SOURCE_SWING_TABLE.partition,
    SOURCE_SWING_TABLE.order,
)
BOUNCE_TABLE = TableContract(
    "trading_assignment_vwap_entry_retest_swing_bounce_v1",
    SOURCE_BOUNCE_TABLE.columns, SOURCE_BOUNCE_TABLE.partition,
    SOURCE_BOUNCE_TABLE.order,
)
TABLES = (RETEST_TABLE, MEMBER_TABLE, SWING_TABLE, BOUNCE_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _number(value: Any, name: str) -> tuple[float | None, int | None]:
    if type(value) is float and isfinite(value):
        return value, None
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return None, value
    raise ValueError(f"VWAP retest {name} must be finite Float64 or Int64")


def validate_typed_retest_anchor(value: Mapping[str, Any]) -> dict[str, Any]:
    """Opt-in producer check for one complete retest witness."""
    from .post_move_entries import validate_typed_breakout_anchor
    if not isinstance(value, Mapping) or set(value) != {
            "anchor", "pivot_at", "pivot_price", "recovered_at", "break_count", "swing"}:
        raise ValueError("VWAP retest witness has missing or unmodeled fields")
    anchor = validate_typed_breakout_anchor(value["anchor"])
    swing = validate_typed_entry_swing(value["swing"])
    for name in ("pivot_at", "pivot_price", "recovered_at"):
        _number(value[name], name)
    if type(value["break_count"]) is not int or not 0 <= value["break_count"] < 2 ** 32:
        raise ValueError("VWAP retest break count is invalid")
    if (value["pivot_at"] != swing["pivot_at"]
            or value["pivot_price"] != swing["price"]
            or value["recovered_at"] < value["pivot_at"]):
        raise ValueError("VWAP retest swing identity or recovery clock differs")
    normalized = dict(anchor)
    for name in ("lower", "upper", "price", "confirmed_at_ms",
                 "grouping_threshold", "broken_at"):
        if name in normalized:
            raw = normalized[name]
            if type(raw) is int and abs(raw) > 2 ** 53:
                raise ValueError(f"VWAP retest anchor {name} loses Float64 precision")
            normalized[name] = float(raw)
    return {**value, "anchor": normalized, "swing": swing}


def project_entry_retest_anchor(value: Mapping[str, Any], *, assignment_id: str,
                                revision: int, snapshot_id: str,
                                session: str) -> dict[str, Any]:
    witness = validate_typed_retest_anchor(value)
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP retest assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP retest snapshot identity is invalid") from exc
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    anchor = witness["anchor"]
    members = anchor.get("members", [])
    if len(members) > 65535:
        raise ValueError("VWAP retest has too many zone members")
    member_rows = [_seal({**identity, "ordinal": ordinal, "member_id": member})
                   for ordinal, member in enumerate(members)]
    swing = project_entry_swing(witness["swing"], **identity)
    mask = sum(1 << ordinal for ordinal, name in enumerate(_ANCHOR_OPTIONAL)
               if name in anchor)
    values = {"anchor_kind": "grouped" if "members" in anchor else "passive",
              "anchor_presence": mask, "anchor_id": anchor["unified_level_id"],
              "anchor_lower": anchor["lower"], "anchor_upper": anchor["upper"],
              "anchor_price": anchor.get("price"), "anchor_side": anchor.get("side"),
              "anchor_role": anchor.get("role"),
              "anchor_confirmed_at_ms": anchor["confirmed_at_ms"],
              "anchor_book_version": anchor["book_version"],
              "anchor_input_policy": anchor.get("input_policy"),
              "anchor_seed_input_policy": anchor.get("seed_input_policy"),
              "anchor_encountered": anchor.get("encountered"),
              "anchor_seen_below": anchor.get("seen_below"),
              "anchor_grouping_threshold": anchor.get("grouping_threshold"),
              "anchor_broken_at": anchor.get("broken_at")}
    numeric = {}
    for name in ("pivot_at", "pivot_price", "recovered_at"):
        numeric[f"{name}_float"], numeric[f"{name}_int"] = _number(witness[name], name)
    parent = _seal({**identity, **numeric, "break_count": witness["break_count"],
                    **values, "member_count": len(member_rows),
                    "member_hash": _hash(member_rows), "swing_hash": _hash(swing)})
    return {"retest": parent, "members": member_rows,
            "swing": swing["swing"], "bounce": swing["bounce"]}


def restore_entry_retest_anchor(rows: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(rows, Mapping) or set(rows) != {"retest", "members", "swing", "bounce"}:
        raise ValueError("VWAP retest rows are incomplete")
    parent, members = rows["retest"], rows["members"]
    if (not isinstance(parent, Mapping)
            or set(parent) != {name for name, _ in RETEST_TABLE.columns}
            or type(members) is not list
            or any(not isinstance(row, Mapping)
                   or set(row) != {name for name, _ in MEMBER_TABLE.columns}
                   for row in members)
            or parent["member_count"] != len(members)
            or parent["member_hash"] != _hash(members)
            or [row["ordinal"] for row in members] != list(range(len(members)))
            or parent["swing_hash"] != _hash({"swing": rows["swing"],
                                               "bounce": rows["bounce"]})):
        raise ValueError("VWAP retest child-family fence differs")
    swing = restore_entry_swing({"swing": rows["swing"], "bounce": rows["bounce"]})
    if swing is None:
        raise ValueError("VWAP retest swing is missing")
    anchor = dict(unified_level_id=parent["anchor_id"], lower=parent["anchor_lower"],
                  upper=parent["anchor_upper"],
                  confirmed_at_ms=parent["anchor_confirmed_at_ms"],
                  book_version=parent["anchor_book_version"])
    columns = ("anchor_price", "anchor_side", "anchor_role", "anchor_input_policy",
               "anchor_seed_input_policy", "anchor_broken_at")
    for ordinal, (name, column) in enumerate(zip(_ANCHOR_OPTIONAL, columns)):
        if parent["anchor_presence"] & (1 << ordinal):
            anchor[name] = parent[column]
        elif parent[column] is not None:
            raise ValueError("VWAP retest anchor field presence differs")
    if parent["anchor_kind"] == "grouped":
        anchor.update(members=[row["member_id"] for row in members],
                      encountered=parent["anchor_encountered"],
                      seen_below=parent["anchor_seen_below"],
                      grouping_threshold=parent["anchor_grouping_threshold"])
    elif (parent["anchor_kind"] != "passive" or members or any(
            parent[key] is not None for key in (
                "anchor_encountered", "anchor_seen_below", "anchor_grouping_threshold"))):
        raise ValueError("VWAP retest anchor family differs")
    witness: dict[str, Any] = {"anchor": anchor, "break_count": parent["break_count"],
                               "swing": swing}
    for name in ("pivot_at", "pivot_price", "recovered_at"):
        as_float, as_int = parent[f"{name}_float"], parent[f"{name}_int"]
        if (as_float is None) == (as_int is None):
            raise ValueError("VWAP retest numeric kind differs")
        witness[name] = as_float if as_float is not None else as_int
    expected = project_entry_retest_anchor(
        witness, assignment_id=parent["assignment_id"], revision=parent["revision"],
        snapshot_id=parent["snapshot_id"], session=parent["session"])
    if expected != rows:
        raise ValueError("VWAP retest content or identity differs")
    return witness
