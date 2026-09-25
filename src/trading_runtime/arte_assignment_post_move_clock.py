"""Inactive complete named-column VWAP post-move entry clock snapshot."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_assignment_pending_breakout import (
    project_pending_breakout, restore_pending_breakout,
)
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
PARENT_TABLE = TableContract(
    "trading_assignment_post_move_clock_v1",
    _KEY + (("present", "Bool"), ("clock_session", "Nullable(Date)"),
            ("price_float", "Nullable(Float64)"), ("price_int", "Nullable(Int64)"),
            ("pullbacks_present", "Bool"), ("moves_present", "Bool"),
            ("pending_present", "Bool"),
            ("pullback_count", "UInt16"), ("pullback_hash", "FixedString(64)"),
            ("move_count", "UInt16"), ("move_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
PULLBACK_TABLE = TableContract(
    "trading_assignment_post_move_consumed_pullback_v1",
    _KEY + (("ordinal", "UInt16"), ("pivot_at_float", "Nullable(Float64)"),
            ("pivot_at_int", "Nullable(Int64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
MOVE_TABLE = TableContract(
    "trading_assignment_post_move_consumed_move_v1",
    _KEY + (("ordinal", "UInt16"), ("move_id_float", "Nullable(Float64)"),
            ("move_id_int", "Nullable(Int64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
TABLES = (PARENT_TABLE, PULLBACK_TABLE, MOVE_TABLE)
_FIELDS = frozenset({"session", "price", "consumed_pullbacks", "consumed_moves",
                     "pending_breakout"})


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _number(value: Any, label: str) -> tuple[float | None, int | None]:
    if type(value) is float and isfinite(value):
        return value, None
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return None, value
    raise ValueError(f"post-move {label} must be finite Float64 or Int64")


def _identity(assignment_id: str, revision: int, snapshot_id: str,
              session: str) -> dict[str, Any]:
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("post-move clock assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("post-move clock snapshot identity is invalid") from exc
    return dict(assignment_id=assignment_id, revision=revision,
                snapshot_id=snapshot_id, session=session)


def project_post_move_clock(value: Any, *, present: bool, assignment_id: str,
                            revision: int, snapshot_id: str,
                            session: str) -> dict[str, Any]:
    identity = _identity(assignment_id, revision, snapshot_id, session)
    if (type(present) is not bool or (not present and value is not None)
            or (present and (not isinstance(value, Mapping) or set(value) - _FIELDS))):
        raise ValueError("post-move clock has unmodeled fields or presence")
    clock = value if present else {}
    clock_session = clock.get("session")
    if clock_session is not None:
        try:
            if type(clock_session) is not str or date.fromisoformat(clock_session).isoformat() != clock_session:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("post-move clock session is invalid") from exc
    elif "session" in clock:
        raise ValueError("post-move clock explicit null session is invalid")
    price_float, price_int = ((None, None) if "price" not in clock
                              else _number(clock["price"], "price"))
    rows = {}
    for field, table, float_name, int_name, label in (
            ("consumed_pullbacks", PULLBACK_TABLE, "pivot_at_float", "pivot_at_int", "pivot"),
            ("consumed_moves", MOVE_TABLE, "move_id_float", "move_id_int", "move ID")):
        values = clock.get(field, [])
        if type(values) is not list or len(values) > 65535:
            raise ValueError(f"post-move {field} must be a bounded ordered list")
        rows[field] = [_seal({**identity, "ordinal": ordinal,
                              float_name: _number(item, label)[0],
                              int_name: _number(item, label)[1]})
                       for ordinal, item in enumerate(values)]
    pending = (project_pending_breakout(clock["pending_breakout"], **identity)
               if "pending_breakout" in clock else None)
    parent = _seal({**identity, "present": present, "clock_session": clock_session,
                    "price_float": price_float, "price_int": price_int,
                    "pullbacks_present": "consumed_pullbacks" in clock,
                    "moves_present": "consumed_moves" in clock,
                    "pending_present": pending is not None,
                    "pullback_count": len(rows["consumed_pullbacks"]),
                    "pullback_hash": _hash(rows["consumed_pullbacks"]),
                    "move_count": len(rows["consumed_moves"]),
                    "move_hash": _hash(rows["consumed_moves"])})
    return {"parent": parent, "pullbacks": rows["consumed_pullbacks"],
            "moves": rows["consumed_moves"], "pending": pending}


def restore_post_move_clock(rows: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if not isinstance(rows, Mapping) or set(rows) != {"parent", "pullbacks", "moves", "pending"}:
        raise ValueError("post-move clock rows are incomplete")
    parent = rows["parent"]
    if not isinstance(parent, Mapping) or set(parent) != {name for name, _ in PARENT_TABLE.columns}:
        raise ValueError("post-move clock parent columns differ")
    clock: dict[str, Any] = {}
    if parent["clock_session"] is not None:
        clock["session"] = parent["clock_session"]
    if parent["price_float"] is not None and parent["price_int"] is not None:
        raise ValueError("post-move clock price kind is ambiguous")
    if parent["price_float"] is not None:
        clock["price"] = parent["price_float"]
    elif parent["price_int"] is not None:
        clock["price"] = parent["price_int"]
    for field, family, table, float_name, int_name, count_name, hash_name, present_name in (
            ("consumed_pullbacks", "pullbacks", PULLBACK_TABLE, "pivot_at_float", "pivot_at_int",
             "pullback_count", "pullback_hash", "pullbacks_present"),
            ("consumed_moves", "moves", MOVE_TABLE, "move_id_float", "move_id_int",
             "move_count", "move_hash", "moves_present")):
        children = rows[family]
        if (type(children) is not list or any(not isinstance(row, Mapping)
                or set(row) != {name for name, _ in table.columns} for row in children)
                or type(parent[present_name]) is not bool
                or (not parent[present_name] and children)
                or parent[count_name] != len(children)
                or parent[hash_name] != _hash(children)
                or [row["ordinal"] for row in children] != list(range(len(children)))):
            raise ValueError(f"post-move {field} rows differ")
        values = []
        for row in children:
            if (row[float_name] is None) == (row[int_name] is None):
                raise ValueError(f"post-move {field} numeric kind differs")
            values.append(row[float_name] if row[float_name] is not None else row[int_name])
        if parent[present_name]:
            clock[field] = values
    if type(parent["pending_present"]) is not bool or parent["pending_present"] != (rows["pending"] is not None):
        raise ValueError("post-move pending presence differs")
    if rows["pending"] is not None:
        clock["pending_breakout"] = restore_pending_breakout(rows["pending"])
    if type(parent["present"]) is not bool or (not parent["present"] and clock):
        raise ValueError("post-move clock presence differs")
    expected = project_post_move_clock(
        clock if parent["present"] else None, present=parent["present"],
        assignment_id=parent["assignment_id"], revision=parent["revision"],
        snapshot_id=parent["snapshot_id"], session=parent["session"])
    if expected != rows:
        raise ValueError("post-move clock content or identity differs")
    return parent["present"], clock
