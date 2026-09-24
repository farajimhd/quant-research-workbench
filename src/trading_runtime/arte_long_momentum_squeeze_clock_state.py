"""Inactive typed scalar-clock slice of squeeze_breakout mutable state.

Caller must select only these keys from squeeze_breakout. Geometry, MACD and
entry state are intentionally outside this contract and must not be dropped.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_FIELDS = {
    "session": "Date", "activated_at": "Float64", "activation_event_id": "String",
    "closed_at": "Float64", "close": "Float64", "hod": "Float64",
    "last_open": "Float64", "trade_price": "Float64", "trade_hod": "Float64",
    "prior_hod": "Float64", "hod_last_trade": "Float64", "late_mode": "Bool",
    "last_entry_fill_at": "Float64", "target_reentry_not_before": "Float64",
    "successor_reentry_last_trade": "Float64",
}
_IDENTITY = (("run_id", "String"), ("assignment_id", "String"),
             ("state_revision", "UInt64"), ("snapshot_session", "Date"))
TABLE = TableContract(
    "trading_assignment_squeeze_breakout_clock_v1",
    _IDENTITY + tuple((f"{key}_present", "Bool") for key in _FIELDS)
    + tuple((key, f"Nullable({kind})") for key, kind in _FIELDS.items())
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(snapshot_session)", "run_id, assignment_id, state_revision",
)


def _identity(*, run_id: str, assignment_id: str, state_revision: int,
              snapshot_session: str) -> dict[str, Any]:
    if (not isinstance(run_id, str) or not run_id or not isinstance(assignment_id, str)
            or not assignment_id or type(state_revision) is not int or state_revision < 0):
        raise ValueError("invalid squeeze clock identity")
    if (not isinstance(snapshot_session, str)
            or date.fromisoformat(snapshot_session).isoformat() != snapshot_session):
        raise ValueError("invalid squeeze clock snapshot session")
    return dict(run_id=run_id, assignment_id=assignment_id,
                state_revision=state_revision, snapshot_session=snapshot_session)


def _value(value: Any, kind: str, key: str) -> Any:
    if value is None:
        return None
    if kind == "Date":
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError(f"invalid {key} date")
        return value
    if kind == "String":
        if not isinstance(value, str):
            raise ValueError(f"invalid {key} string")
        return value
    if kind == "Bool":
        if type(value) is not bool:
            raise ValueError(f"invalid {key} bool")
        return value
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"invalid {key} finite number")
    return float(value)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_squeeze_breakout_clock(clock: Mapping[str, Any], *, run_id: str,
                                   assignment_id: str, state_revision: int,
                                   snapshot_session: str) -> dict[str, Any]:
    """Project an explicitly selected, closed subset of breakout scalar state."""
    identity = _identity(run_id=run_id, assignment_id=assignment_id,
                         state_revision=state_revision, snapshot_session=snapshot_session)
    if not isinstance(clock, Mapping) or set(clock) - set(_FIELDS):
        raise ValueError("squeeze breakout clock has unmodeled fields")
    if "session" in clock and clock["session"] != snapshot_session:
        raise ValueError("squeeze breakout clock session differs from snapshot")
    return _seal({
        **identity,
        **{f"{key}_present": key in clock for key in _FIELDS},
        **{key: _value(clock[key], kind, key) if key in clock else None
           for key, kind in _FIELDS.items()},
    })


def restore_squeeze_breakout_clock(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != {key for key, _ in TABLE.columns}:
        raise ValueError("squeeze breakout clock row has missing or unmodeled columns")
    identity = {key: row[key] for key, _ in _IDENTITY}
    clock = {}
    for key in _FIELDS:
        present = row[f"{key}_present"]
        if type(present) is not bool or not present and row[key] is not None:
            raise ValueError("invalid squeeze clock presence flag")
        if present:
            clock[key] = row[key]
    if project_squeeze_breakout_clock(clock, **identity) != row:
        raise ValueError("squeeze breakout clock content hash or canonical row mismatch")
    return clock
