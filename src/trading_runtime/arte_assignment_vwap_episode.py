"""Inactive closed VWAP ladder MACD-episode scalar state.

This is only ``vwap_ladder_episode``; entry/clock/market state remains rejected.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


FIELDS = {"session": "Nullable(Date)", "sample_at": "Nullable(Float64)",
          "bullish": "Nullable(Bool)", "used": "Nullable(Bool)",
          "started_at": "Nullable(Float64)"}
TABLE = TableContract(
    "trading_assignment_vwap_episode_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"),
     ("present", "Bool"), ("episode_session", FIELDS["session"]),
     *((name, kind) for name, kind in FIELDS.items() if name != "session"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode()).hexdigest()


def project_vwap_episode(value: Any, *, present: bool,
                         assignment_id: str, revision: int,
                         snapshot_id: str, session: str) -> dict[str, Any]:
    if (type(present) is not bool or (not present and value is not None)
            or (present and (not isinstance(value, Mapping) or set(value) - set(FIELDS)))):
        raise ValueError("VWAP episode presence or fields differ")
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP episode assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP episode snapshot identity is invalid") from exc
    source = value if present else {}
    for name, item in source.items():
        if name == "session":
            try:
                valid = type(item) is str and date.fromisoformat(item).isoformat() == item
            except ValueError:
                valid = False
        elif name in ("sample_at", "started_at"):
            valid = type(item) is float and isfinite(item)
        else:
            valid = type(item) is bool
        if not valid:
            raise ValueError(f"VWAP episode {name} has unmodeled value")
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session, present=present,
               episode_session=source.get("session"),
               **{key: source.get(key) for key in FIELDS if key != "session"})
    return {**row, "content_hash": _hash(row)}


def restore_vwap_episode(row: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("VWAP episode columns differ")
    if type(row["present"]) is not bool:
        raise ValueError("VWAP episode presence differs")
    source = {}
    if row["episode_session"] is not None:
        source["session"] = row["episode_session"]
    source.update({key: row[key] for key in FIELDS if key != "session"
                   and row[key] is not None})
    if not row["present"] and source:
        raise ValueError("VWAP episode orphan fields")
    expected = project_vwap_episode(source if row["present"] else None,
                                    present=row["present"],
                                    assignment_id=row["assignment_id"],
                                    revision=row["revision"],
                                    snapshot_id=row["snapshot_id"],
                                    session=row["session"])
    if expected != row:
        raise ValueError("VWAP episode content or identity differs")
    return row["present"], source
