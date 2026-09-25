"""Inactive closed V5 acquired/held MACD episode identities.

These are top-level episode clocks, not the variant-dependent
``v5_breakout_state`` or ``v5_entry_selection`` evidence trees.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


FIELDS = {"last_acquired_macd_episode": "acquired_at",
          "last_held_macd_episode": "held_at"}
TABLE = TableContract(
    "trading_assignment_v5_episode_identity_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"),
     ("acquired_at", "Nullable(Float64)"),
     ("held_at", "Nullable(Float64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)


def validate_v5_episode_identity(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) - set(FIELDS):
        raise ValueError("V5 episode identity has unmodeled fields")
    for name, clock in value.items():
        if type(clock) is not float or not isfinite(clock) or clock <= 0:
            raise ValueError(f"V5 episode {name} must be positive finite Float64")
    return dict(value)


def project_v5_episode_identity(value: Mapping[str, Any], *, assignment_id: str,
                                revision: int, snapshot_id: str,
                                session: str) -> dict[str, Any] | None:
    source = validate_v5_episode_identity(value)
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("V5 episode assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("V5 episode snapshot identity is invalid") from exc
    if not source:
        return None
    row = dict(assignment_id=assignment_id, revision=revision,
               snapshot_id=snapshot_id, session=session,
               **{column: source.get(name) for name, column in FIELDS.items()})
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def restore_v5_episode_identity(row: Mapping[str, Any] | None) -> dict[str, float]:
    if row is None:
        return {}
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("V5 episode row columns differ")
    source = {name: row[column] for name, column in FIELDS.items()
              if row[column] is not None}
    expected = project_v5_episode_identity(
        source, assignment_id=row["assignment_id"], revision=row["revision"],
        snapshot_id=row["snapshot_id"], session=row["session"])
    if expected != row:
        raise ValueError("V5 episode row content or identity differs")
    return source
