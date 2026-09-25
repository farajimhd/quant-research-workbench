"""Inactive typed base-assignment revision contract.

This records only closed scalar fields. Parameter and state content must be
published and attested separately before a revision can become live authority.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Mapping, Sequence

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)


BASE_REVISION = TableContract(
    "live_strategy_assignment_base_revision_typed_v1",
    (("schema_version", "UInt16"), ("assignment_id", "String"),
     ("revision_sequence", "UInt64"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("account_id", "String"),
     ("ticker", "String"), ("conid", "UInt64"), ("status", "LowCardinality(String)"),
     ("can_observe", "Bool"), ("can_enter", "Bool"), ("can_add", "Bool"),
     ("can_reduce", "Bool"), ("can_exit", "Bool"), ("can_reenter", "Bool"),
     ("source", "String"), ("created_at", "DateTime64(6, 'UTC')"),
     ("updated_at", "DateTime64(6, 'UTC')"),
     ("parameter_content_hash", "FixedString(64)"),
     ("state_content_hash", "FixedString(64)"),
     ("previous_revision_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "cityHash64(assignment_id) % 64", "assignment_id, revision_sequence",
)


def _hash(value: Mapping[str, Any]) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _time(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("assignment timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _digest(value: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("assignment content hash is invalid")
    return value


def project_base_revision(
    assignment: StrategyAssignment, *, revision_sequence: int,
    parameter_content_hash: str, state_content_hash: str,
    previous_revision_hash: str,
) -> dict[str, Any]:
    """Seal the exact route-compatible scalar base, excluding child content."""
    if not isinstance(assignment, StrategyAssignment) or type(revision_sequence) is not int or revision_sequence < 1:
        raise ValueError("assignment revision is invalid")
    if (any(type(getattr(assignment, key)) is not str or not getattr(assignment, key)
            for key in ("assignment_id", "strategy_id", "account_id", "ticker"))
            or type(assignment.strategy_revision) is not int or assignment.strategy_revision < 1
            or type(assignment.conid) is not int or assignment.conid < 1):
        raise ValueError("assignment base identity is invalid")
    if (revision_sequence == 1 and previous_revision_hash != "0" * 64) or (
        revision_sequence > 1 and previous_revision_hash == "0" * 64
    ):
        raise ValueError("assignment revision predecessor is invalid")
    if any(type(getattr(assignment.permissions, key)) is not bool for key in
           ("observe", "enter", "add", "reduce", "exit", "reenter")):
        raise ValueError("assignment permissions must be Boolean")
    if not isinstance(assignment.status, AssignmentStatus) or type(assignment.source) is not str:
        raise ValueError("assignment status or source is invalid")
    row = dict(
        schema_version=1, assignment_id=assignment.assignment_id,
        revision_sequence=revision_sequence, strategy_id=assignment.strategy_id,
        strategy_revision=assignment.strategy_revision, account_id=assignment.account_id,
        ticker=assignment.ticker, conid=assignment.conid, status=assignment.status.value,
        can_observe=assignment.permissions.observe, can_enter=assignment.permissions.enter,
        can_add=assignment.permissions.add, can_reduce=assignment.permissions.reduce,
        can_exit=assignment.permissions.exit, can_reenter=assignment.permissions.reenter,
        source=assignment.source, created_at=_time(assignment.created_at),
        updated_at=_time(assignment.updated_at),
        parameter_content_hash=_digest(parameter_content_hash),
        state_content_hash=_digest(state_content_hash),
        previous_revision_hash=_digest(previous_revision_hash),
    )
    if row["updated_at"] < row["created_at"]:
        raise ValueError("assignment update precedes creation")
    return {**row, "content_hash": _hash(row)}


def recover_base_revision(
    rows: Sequence[Mapping[str, Any]], *, expected_assignment_id: str,
    expected_sequence: int, expected_hash: str,
    parameter_content_hash: str, state_content_hash: str,
    previous_revision_hash: str,
) -> dict[str, Any]:
    """Verify an externally attested exact revision; no latest-row guessing."""
    if len(rows) != 1:
        raise ValueError("assignment base revision missing or duplicated")
    row = dict(rows[0])
    if set(row) != {name for name, _ in BASE_REVISION.columns}:
        raise ValueError("assignment base revision columns differ")
    if (row["assignment_id"] != expected_assignment_id or
            row["revision_sequence"] != expected_sequence or
            row["content_hash"] != _digest(expected_hash) or
            row["parameter_content_hash"] != _digest(parameter_content_hash) or
            row["state_content_hash"] != _digest(state_content_hash) or
            row["previous_revision_hash"] != _digest(previous_revision_hash)):
        raise ValueError("assignment base revision attestation differs")
    try:
        assignment = StrategyAssignment(
            assignment_id=row["assignment_id"], strategy_id=row["strategy_id"],
            strategy_revision=row["strategy_revision"], account_id=row["account_id"],
            ticker=row["ticker"], conid=row["conid"], status=AssignmentStatus(row["status"]),
            permissions=StrategyPermissions(
                observe=row["can_observe"], enter=row["can_enter"], add=row["can_add"],
                reduce=row["can_reduce"], exit=row["can_exit"], reenter=row["can_reenter"]),
            parameters={}, state={}, source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        exact = project_base_revision(
            assignment, revision_sequence=expected_sequence,
            parameter_content_hash=parameter_content_hash,
            state_content_hash=state_content_hash,
            previous_revision_hash=previous_revision_hash,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("assignment base revision is invalid") from exc
    if row != exact:
        raise ValueError("assignment base revision content differs")
    return row
