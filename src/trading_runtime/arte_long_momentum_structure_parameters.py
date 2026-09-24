"""Inactive named typed structural-entry and momentum-management families."""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION


_REVISIONS = frozenset((*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
_STRUCTURAL = {
    "acceptance_buffer_bps": "Float64", "acceptance_hold_ms": "UInt64",
    "enabled": "UInt8", "entry_tranche_count": "UInt32",
    "follow_current_level_prices": "UInt8", "intrabar_after_completed_r3": "UInt8",
    "maximum_entry_levels": "UInt32", "minimum_confidence": "Float64",
    "minimum_hold_observations": "UInt32", "minimum_hold_probability": "Float64",
    "minimum_hold_quality_score": "Float64", "minimum_reaction_probability": "Float64",
    "minimum_salience": "Float64", "minimum_ticker_relative_quality_score": "Float64",
    "persistent_r3_acceptance": "UInt8", "retain_crossing_role_flip": "UInt8",
    "selection_mode": "String", "strict_ticker_relative_quality_gate": "UInt8",
    "use_available_entry_resistances": "UInt8",
}
_MOMENTUM = {
    "downside_loss_guard": {
        "below_vwap": "UInt8", "enabled": "UInt8", "macd_closed": "UInt8",
        "timeframe": "String", "vwap_source_id": "String",
    },
    "failure_to_extend": {
        "enabled": "UInt8", "maximum_flow_structure_score": "Float64",
        "maximum_uses": "UInt32", "minimum_extension_bps": "Float64",
        "minimum_flow_price_divergence_score": "Float64", "minimum_gain_pct": "Float64",
        "position_fraction": "Float64", "stalled_for_ms": "UInt64",
    },
    "macd_backstop": {
        "active_after_ms": "UInt64", "close_condition": "String",
        "closed_for_ms": "UInt64", "enabled": "UInt8", "timeframe": "String",
    },
    "qmd_exhaustion": {
        "active_after_ms": "UInt64", "enabled": "UInt8",
        "maximum_flow_structure_score": "Float64", "minimum_confidence": "Float64",
        "minimum_flow_price_divergence_score": "Float64",
    },
    "structure_failure": {
        "active_after_ms": "UInt64", "buffer_bps": "Float64",
        "enabled": "UInt8", "require_higher_low": "UInt8",
    },
}
_MOMENTUM_SCALAR = {"minimum_macd_exit_gap_bps": "Float64"}
_KEY = (("assignment_id", "String"), ("strategy_id", "String"),
        ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"), ("session", "Date"))
_MOMENTUM_COLUMNS = {f"{section}_{field}": kind for section, fields in _MOMENTUM.items()
                     for field, kind in fields.items()} | _MOMENTUM_SCALAR
TABLES = (
    TableContract("trading_assignment_long_momentum_structural_entry_v1",
                  _KEY + tuple((key, f"Nullable({kind})") for key, kind in _STRUCTURAL.items())
                  + (("content_hash", "FixedString(64)"),),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id"),
    TableContract("trading_assignment_long_momentum_management_v1",
                  _KEY + tuple((key, f"Nullable({kind})") for key, kind in _MOMENTUM_COLUMNS.items())
                  + (("content_hash", "FixedString(64)"),),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id"),
)


def _typed(value: Any, kind: str, label: str) -> Any:
    if kind == "UInt8":
        if type(value) is not bool:
            raise ValueError(f"{label} must be boolean")
        return int(value)
    if kind.startswith("UInt"):
        if type(value) is not int or value < 0:
            raise ValueError(f"{label} must be nonnegative integer")
        return value
    if kind == "Float64":
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"{label} must be finite numeric")
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    return value


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def project_structure_parameters(
    families: Mapping[str, Any], *, assignment_id: str, strategy_id: str,
    strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, dict[str, Any]]:
    if (not assignment_id or strategy_id != STRATEGY_ID or type(strategy_revision) is not int
            or strategy_revision not in _REVISIONS):
        raise ValueError("structure parameter identity/revision is unsupported")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("structure parameter session is invalid")
    if not isinstance(families, Mapping) or set(families) != {"structural_entry", "momentum_management"}:
        raise ValueError("structure parameter families are incomplete")
    structural = families["structural_entry"]
    momentum = families["momentum_management"]
    if not isinstance(structural, Mapping) or set(structural) - set(_STRUCTURAL):
        raise ValueError("structural entry has unmodeled fields")
    if not isinstance(momentum, Mapping) or set(momentum) - (set(_MOMENTUM) | set(_MOMENTUM_SCALAR)):
        raise ValueError("momentum management has unmodeled fields")
    if set(_MOMENTUM) - set(momentum):
        raise ValueError("momentum management sections are incomplete")
    common = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                  strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    structural_row = {key: _typed(structural[key], kind, key) if key in structural else None
                      for key, kind in _STRUCTURAL.items()}
    momentum_row = {
        key: _typed(momentum[key], kind, key) if key in momentum else None
        for key, kind in _MOMENTUM_SCALAR.items()
    }
    for section, fields in _MOMENTUM.items():
        source = momentum[section]
        if not isinstance(source, Mapping) or set(source) - set(fields):
            raise ValueError(f"momentum {section} has unmodeled fields")
        momentum_row.update({
            f"{section}_{field}": _typed(source[field], kind, f"{section}.{field}")
            if field in source else None for field, kind in fields.items()
        })
    return {"structural_entry": _seal({**common, **structural_row}),
            "momentum_management": _seal({**common, **momentum_row})}


def restore_structure_parameters(rows: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    if set(rows) != {"structural_entry", "momentum_management"}:
        raise ValueError("structure parameter typed rows are incomplete")
    for table, row in zip(TABLES, (rows["structural_entry"], rows["momentum_management"])):
        if set(row) != {name for name, _ in table.columns}:
            raise ValueError("structure parameter columns differ")
    first = rows["structural_entry"]
    common = {key: first[key] for key, _ in _KEY}
    if any(rows["momentum_management"][key] != value for key, value in common.items()):
        raise ValueError("structure parameter snapshot fence differs")
    structural = {key: bool(first[key]) if kind == "UInt8" else first[key]
                  for key, kind in _STRUCTURAL.items() if first[key] is not None}
    momentum_row = rows["momentum_management"]
    momentum: dict[str, Any] = {
        key: momentum_row[key] for key in _MOMENTUM_SCALAR if momentum_row[key] is not None
    }
    for section, fields in _MOMENTUM.items():
        momentum[section] = {
            field: bool(momentum_row[f"{section}_{field}"]) if kind == "UInt8"
            else momentum_row[f"{section}_{field}"]
            for field, kind in fields.items() if momentum_row[f"{section}_{field}"] is not None
        }
    result = {"structural_entry": structural, "momentum_management": momentum}
    if project_structure_parameters(result, **common) != rows:
        raise ValueError("structure parameter content hash mismatch")
    return result
