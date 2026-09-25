"""Inactive typed observation clock and price state for live Long Momentum.

The current producer calls datetime.isoformat(). This typed-only boundary
normalizes UTC zero-microsecond input to six-digit DateTime64 form without
changing the causal instant or mutating the legacy strategy state.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from math import isfinite
import re
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


FIELDS = ("last_observed_at", "last_price", "previous_observed_price")
TABLE = TableContract(
    "trading_assignment_observation_clock_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"),
     ("last_observed_at_present", "Bool"),
     ("last_observed_at", "Nullable(DateTime64(6, 'UTC'))"),
     ("last_price_present", "Bool"), ("last_price", "Nullable(Float64)"),
     ("previous_observed_price_present", "Bool"),
     ("previous_observed_price", "Nullable(Float64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def _clock(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("observation clock must be UTC ISO text")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?\+00:00", value):
        raise ValueError("observation clock must use producer UTC ISO form")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("observation clock is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("observation clock must be UTC")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


def normalize_observation_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize only this typed journal field without mutating live state."""
    if not isinstance(state, Mapping):
        raise ValueError("assignment state is invalid")
    result = dict(state)
    if "last_observed_at" in result:
        result["last_observed_at"] = _clock(result["last_observed_at"])
    return result


def project_observation_clock(values: Mapping[str, Any], *, assignment_id: str,
                              revision: int, snapshot_id: str,
                              session: str) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) - set(FIELDS):
        raise ValueError("observation clock has unmodeled fields")
    if (type(assignment_id) is not str or not assignment_id
            or type(revision) is not int or revision < 1):
        raise ValueError("observation clock identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("observation clock snapshot is invalid") from exc
    normalized_clock = _clock(values["last_observed_at"]) if "last_observed_at" in values else None
    for name in ("last_price", "previous_observed_price"):
        if name in values:
            value = values[name]
            if value is not None and (type(value) is not float or not isfinite(value)):
                raise ValueError(f"observation {name} must be finite Float64 or None")
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session)
    row.update({f"{name}_present": name in values for name in FIELDS})
    row.update({name: (normalized_clock if name == "last_observed_at" else values.get(name))
                for name in FIELDS})
    return _seal(row)


def restore_observation_clock(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("observation clock columns differ")
    values = {}
    for name in FIELDS:
        present = row[f"{name}_present"]
        if type(present) is not bool or (not present and row[name] is not None):
            raise ValueError("observation clock presence differs")
        if present:
            values[name] = row[name]
    expected = project_observation_clock(
        values, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if dict(row) != expected:
        raise ValueError("observation clock content differs")
    return values
