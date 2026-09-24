"""Inactive normalized Long Momentum liquidity and entry-confirmation parameters."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION


_REVISIONS = frozenset((*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
_FIELDS = {
    "liquidity_admission": {
        "enabled": "Bool", "latched": "Bool", "minimum_price": "Float64",
        "maximum_price": "Float64", "minimum_session_dollar_volume": "Float64",
        "minimum_session_share_volume": "Float64", "minimum_trade_rate_10s": "Float64",
        "minimum_trade_rate_60s": "Float64", "maximum_admission_spread_bps": "Float64",
        "maximum_current_spread_bps": "Float64", "maximum_spread_bps": "Float64",
    },
    "entry_momentum_confirmation": {
        "enabled": "Bool", "timeframe": "String", "histogram_lookback_ms": "UInt32",
        "minimum_histogram_increase": "Float64", "minimum_histogram_increase_bps": "Float64",
    },
    "entry_candle_confirmation": {
        "enabled": "Bool", "timeframe": "String", "require_closed_bar": "Bool",
        "reject_bearish_close": "Bool", "minimum_macd_open_gap_bps": "Float64",
        "evaluate_macd_intrabar": "Bool", "slope_reentry_break_previous_high": "Bool",
        "minimum_reentry_macd_gap_bps": "Float64",
    },
}
_OPTIONAL = frozenset({"evaluate_macd_intrabar", "slope_reentry_break_previous_high",
                       "minimum_reentry_macd_gap_bps"})
_IDENTITY = (("assignment_id", "String"), ("strategy_id", "String"),
             ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"),
             ("session", "Date"))
TABLES = {
    family: TableContract(
        f"trading_assignment_long_momentum_{family}_parameters_v1",
        _IDENTITY + tuple((key, f"Nullable({kind})" if key in _OPTIONAL else kind)
                          for key, kind in fields.items())
        + (("content_hash", "FixedString(64)"),),
        "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id",
    ) for family, fields in _FIELDS.items()
}


def _identity(*, assignment_id: str, strategy_id: str, strategy_revision: int,
              snapshot_id: str, session: str) -> dict[str, Any]:
    if (not isinstance(assignment_id, str) or not assignment_id or strategy_id != STRATEGY_ID
            or type(strategy_revision) is not int or strategy_revision not in _REVISIONS):
        raise ValueError("unsupported assignment identity/revision")
    UUID(snapshot_id)
    if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
        raise ValueError("invalid assignment session")
    return dict(assignment_id=assignment_id, strategy_id=strategy_id,
                strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)


def _scalar(value: Any, kind: str, key: str) -> Any:
    if kind == "Bool":
        if type(value) is not bool:
            raise ValueError(f"{key} must be bool")
        return value
    if kind == "String":
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be nonempty string")
        return value
    if kind == "UInt32":
        if type(value) is not int or not 0 <= value < 2**32:
            raise ValueError(f"{key} must be unsigned integer")
        return value
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{key} must be finite numeric")
    return float(value)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_entry_confirm_parameters(
    sections: Mapping[str, Mapping[str, Any]], *, assignment_id: str,
    strategy_id: str, strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, dict[str, Any]]:
    """Emit one strict named-column row per resolver family."""
    identity = _identity(assignment_id=assignment_id, strategy_id=strategy_id,
                         strategy_revision=strategy_revision, snapshot_id=snapshot_id,
                         session=session)
    if not isinstance(sections, Mapping) or set(sections) != set(_FIELDS):
        raise ValueError("missing or unmodeled entry-confirmation family")
    projected = {}
    for family, spec in _FIELDS.items():
        values = sections[family]
        if (not isinstance(values, Mapping) or not set(spec) - _OPTIONAL <= set(values)
                or set(values) - set(spec)):
            raise ValueError(f"{family} has missing or unmodeled fields")
        projected[family] = _seal({
            **identity,
            **{key: _scalar(values[key], kind, key) if key in values else None
               for key, kind in spec.items()},
        })
    return projected


def restore_entry_confirm_parameters(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Validate every column, row hash and shared identity before restoring."""
    if not isinstance(rows, Mapping) or set(rows) != set(_FIELDS):
        raise ValueError("missing or unmodeled entry-confirmation rows")
    if not isinstance(rows["liquidity_admission"], Mapping):
        raise ValueError("invalid liquidity row")
    identity = {key: rows["liquidity_admission"][key] for key, _ in _IDENTITY}
    values = {}
    for family, spec in _FIELDS.items():
        row = rows[family]
        if not isinstance(row, Mapping) or set(row) != {key for key, _ in TABLES[family].columns}:
            raise ValueError(f"{family} row has missing or unmodeled columns")
        if any(row[key] != value for key, value in identity.items()):
            raise ValueError("mixed assignment identities")
        if row != _seal({key: value for key, value in row.items() if key != "content_hash"}):
            raise ValueError(f"{family} content hash mismatch")
        values[family] = {key: row[key] for key in spec if key not in _OPTIONAL or row[key] is not None}
    if rows != project_entry_confirm_parameters(values, **identity):
        raise ValueError("noncanonical entry-confirmation rows")
    return values
