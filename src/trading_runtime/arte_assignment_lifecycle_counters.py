"""Inactive closed typed counters written by the built-in live strategy.

Presence is independent of value: an absent counter is not equivalent to 0.
No dynamic key/value rows or generic state serialization are admitted.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID
from datetime import date

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


COUNTERS = ("entries", "adds", "reentries", "profit_takes")
TABLE = TableContract(
    "trading_assignment_lifecycle_counters_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"))
    + tuple((f"{name}_present", "Bool") for name in COUNTERS)
    + tuple((name, "Nullable(UInt64)") for name in COUNTERS)
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_lifecycle_counters(
    values: Mapping[str, Any], *, assignment_id: str,
    revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) - set(COUNTERS):
        raise ValueError("assignment lifecycle counters have unmodeled fields")
    if (type(assignment_id) is not str or not assignment_id
            or type(revision) is not int or revision < 1):
        raise ValueError("assignment lifecycle counter identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("assignment lifecycle counter snapshot is invalid") from exc
    for name, value in values.items():
        if type(value) is not int or value < 0 or value >= 2 ** 64:
            raise ValueError(f"assignment lifecycle counter {name} is invalid")
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session)
    row.update({f"{name}_present": name in values for name in COUNTERS})
    row.update({name: values.get(name) for name in COUNTERS})
    return _seal(row)


def restore_lifecycle_counters(row: Mapping[str, Any]) -> dict[str, int]:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("assignment lifecycle counter columns differ")
    values = {}
    for name in COUNTERS:
        present = row[f"{name}_present"]
        value = row[name]
        if type(present) is not bool or (present and value is None) or (
                not present and value is not None):
            raise ValueError("assignment lifecycle counter presence differs")
        if present:
            values[name] = value
    expected = project_lifecycle_counters(
        values, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if dict(row) != expected:
        raise ValueError("assignment lifecycle counter content differs")
    return values
