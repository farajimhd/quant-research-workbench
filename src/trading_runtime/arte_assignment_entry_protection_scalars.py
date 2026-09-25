"""Inactive closed typed entry/protection numeric state for Long Momentum.

These are the named scalar fields set together on confirmed entry. Presence,
explicit None, and a finite Float64 value remain distinct on cold recovery.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


FIELDS = (
    "breakout_level", "breakout_buffer_bps", "entry_reference_price",
    "initial_stop", "active_stop", "trailing_amount",
    "high_water_price", "low_water_price",
)
TABLE = TableContract(
    "trading_assignment_entry_protection_scalar_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"))
    + tuple((f"{name}_present", "Bool") for name in FIELDS)
    + tuple((name, "Nullable(Float64)") for name in FIELDS)
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_entry_protection_scalars(
    values: Mapping[str, Any], *, assignment_id: str,
    revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) - set(FIELDS):
        raise ValueError("entry protection scalars have unmodeled fields")
    if (type(assignment_id) is not str or not assignment_id
            or type(revision) is not int or revision < 1):
        raise ValueError("entry protection scalar identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("entry protection scalar snapshot is invalid") from exc
    for name, value in values.items():
        if value is not None and (type(value) is not float or not isfinite(value)):
            raise ValueError(f"entry protection scalar {name} must be finite Float64 or None")
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session)
    row.update({f"{name}_present": name in values for name in FIELDS})
    row.update({name: values.get(name) for name in FIELDS})
    return _seal(row)


def restore_entry_protection_scalars(row: Mapping[str, Any]) -> dict[str, float | None]:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("entry protection scalar columns differ")
    values = {}
    for name in FIELDS:
        present = row[f"{name}_present"]
        if type(present) is not bool or (not present and row[name] is not None):
            raise ValueError("entry protection scalar presence differs")
        if present:
            values[name] = row[name]
    expected = project_entry_protection_scalars(
        values, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if dict(row) != expected:
        raise ValueError("entry protection scalar content differs")
    return values
