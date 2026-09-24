"""Inactive typed execution-parameter family for Long Momentum revisions 26-47.

This is one closed child family, not a complete assignment parameter journal.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import (
    HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION,
)


_REVISIONS = frozenset((*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
_FIELDS = frozenset({"entry_urgency", "exit_urgency", "limit_offset_bps", "tick_size"})
_URGENCIES = frozenset({
    "aggressive_limit", "market", "patient", "regular", "urgent", "very_urgent",
})
TABLE = TableContract(
    "trading_assignment_long_momentum_execution_parameters_v1",
    (("assignment_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"),
     ("session", "Date"), ("entry_urgency", "LowCardinality(String)"),
     ("exit_urgency", "LowCardinality(String)"),
     ("limit_offset_bps", "Float64"), ("tick_size", "Float64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id",
)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_execution_parameters(
    execution: Mapping[str, Any], *, assignment_id: str,
    strategy_id: str, strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Project exactly the resolver's execution subfamily, not arbitrary overrides."""
    if (not assignment_id or strategy_id != STRATEGY_ID
            or strategy_revision not in _REVISIONS or type(strategy_revision) is not int):
        raise ValueError("execution parameter strategy identity/revision is unsupported")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("execution parameter session is invalid")
    if not isinstance(execution, Mapping) or set(execution) != _FIELDS:
        raise ValueError("execution parameters have missing or unmodeled fields")
    for key in ("entry_urgency", "exit_urgency"):
        if not isinstance(execution[key], str) or execution[key] not in _URGENCIES:
            raise ValueError(f"execution parameter {key} is unsupported")
    for key in ("limit_offset_bps", "tick_size"):
        if type(execution[key]) not in (int, float) or not isfinite(execution[key]):
            raise ValueError(f"execution parameter {key} must be finite numeric")
    if execution["tick_size"] <= 0:
        raise ValueError("execution parameter tick_size must be positive")
    return _seal({
        "assignment_id": assignment_id, "strategy_id": strategy_id,
        "strategy_revision": strategy_revision, "snapshot_id": snapshot_id,
        "session": session, "entry_urgency": execution["entry_urgency"],
        "exit_urgency": execution["exit_urgency"],
        "limit_offset_bps": float(execution["limit_offset_bps"]),
        "tick_size": float(execution["tick_size"]),
    })


def restore_execution_parameters(row: Mapping[str, Any]) -> dict[str, Any]:
    """Cold-read and validate every column, identity, type and row hash."""
    if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLE.columns}:
        raise ValueError("execution parameter row has missing or unmodeled columns")
    values = {key: row[key] for key in _FIELDS}
    expected = project_execution_parameters(
        values, assignment_id=row["assignment_id"], strategy_id=row["strategy_id"],
        strategy_revision=row["strategy_revision"], snapshot_id=row["snapshot_id"],
        session=row["session"],
    )
    if expected != row:
        raise ValueError("execution parameter row content hash mismatch")
    return values
