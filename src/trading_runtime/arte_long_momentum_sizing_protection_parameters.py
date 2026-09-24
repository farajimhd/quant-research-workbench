"""Inactive, normalized Long Momentum sizing/protection assignment parameters."""
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
_SPECS = {
    "sizing": {
        "request_mode": "String", "request_value": "Float64",
        "initial_quantity": "Float64", "add_fraction": "Float64",
    },
    "stop": {
        "method": "String", "require_qualified_support": "Bool",
        "structure_buffer_bps": "Float64", "volatility_multiple": "Float64",
        "maximum_risk_pct": "Float64", "minimum_ticker_relative_quality_score": "Float64",
        "strict_ticker_relative_quality_gate": "Bool", "minimum_hold_probability": "Float64",
        "minimum_hold_quality_score": "Float64", "minimum_hold_observations": "UInt32",
        "support_level_ordinal": "UInt32", "prefer_closer_hybrid": "Bool",
        "cap_initial_stop_distance": "Bool",
    },
    "trailing": {
        "enabled": "Bool", "mode": "String", "activation_gain_pct": "Float64",
        "distance_volatility_multiple": "Float64", "minimum_distance_bps": "Float64",
    },
    "profit_ladder": {
        "enabled": "Bool", "maximum_targets": "UInt32", "minimum_spacing_bps": "Float64",
        "minimum_level_strength": "Float64", "minimum_level_confidence": "Float64",
        "minimum_reaction_probability": "Float64", "minimum_reversal_probability": "Float64",
        "minimum_ticker_relative_quality_score": "Float64",
        "strict_ticker_relative_quality_gate": "Bool", "minimum_hold_probability": "Float64",
        "minimum_hold_quality_score": "Float64", "minimum_hold_observations": "UInt32",
        "minimum_composite_score": "Float64", "minimum_entry_target_gap_bps": "Float64",
        "premarket_maximum_gain_pct": "Float64", "selection_mode": "String",
        "target_level_ordinal": "UInt32", "require_resistance_role": "Bool",
    },
    "luld_profit_target": {
        "enabled": "Bool", "buffer_bps": "Float64", "minimum_tick_offset_count": "UInt32",
        "tick_size": "Float64", "include_current_spread": "Bool",
        "require_authoritative_band": "Bool",
    },
}
_IDENTITY = (("assignment_id", "String"), ("strategy_id", "String"),
             ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"),
             ("session", "Date"))
TABLES = {
    family: TableContract(
        f"trading_assignment_long_momentum_{family}_parameters_v1",
        _IDENTITY + tuple((key, f"Nullable({kind})") for key, kind in fields.items())
        + ((("risk_multiple_count", "UInt32"), ("risk_multiple_set_hash", "FixedString(64)"))
           if family == "profit_ladder" else ())
        + (("content_hash", "FixedString(64)"),),
        "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id",
    ) for family, fields in _SPECS.items()
}
RISK_MULTIPLE_TABLE = TableContract(
    "trading_assignment_long_momentum_profit_risk_multiple_v1",
    _IDENTITY + (("ordinal", "UInt32"), ("risk_multiple", "Float64"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id, ordinal",
)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def _identity(assignment_id: str, strategy_id: str, strategy_revision: int,
              snapshot_id: str, session: str) -> dict[str, Any]:
    if (not isinstance(assignment_id, str) or not assignment_id
            or strategy_id != STRATEGY_ID or type(strategy_revision) is not int
            or strategy_revision not in _REVISIONS):
        raise ValueError("unsupported sizing/protection assignment identity")
    UUID(snapshot_id)
    from datetime import date
    if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
        raise ValueError("invalid session")
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


def project_sizing_protection_parameters(
    sizing: Mapping[str, Any], protection: Mapping[str, Any], *,
    assignment_id: str, strategy_id: str, strategy_revision: int,
    snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Project exact resolver family shapes into sealed named-column rows."""
    identity = _identity(assignment_id, strategy_id, strategy_revision, snapshot_id, session)
    if not isinstance(protection, Mapping) or set(protection) != set(_SPECS) - {"sizing"}:
        raise ValueError("protection has missing or unmodeled families")
    source = {"sizing": sizing, **protection}
    result: dict[str, Any] = {}
    for family, spec in _SPECS.items():
        values = source[family]
        if not isinstance(values, Mapping):
            raise ValueError(f"{family} must be mapping")
        required = set(spec)
        if family in {"stop", "profit_ladder"}:
            quality = ({"minimum_hold_probability", "minimum_hold_quality_score"}
                       if strategy_revision <= 33 else
                       {"minimum_ticker_relative_quality_score", "strict_ticker_relative_quality_gate"})
            required -= ({"minimum_hold_probability", "minimum_hold_quality_score",
                          "minimum_ticker_relative_quality_score", "strict_ticker_relative_quality_gate"} - quality)
            if family == "stop":
                if strategy_revision < 45:
                    required.remove("cap_initial_stop_distance")
            else:
                if strategy_revision < 37:
                    required -= {"selection_mode", "target_level_ordinal", "require_resistance_role"}
        if family == "profit_ladder":
            required.add("risk_multiples")
        permitted = set(spec) | ({"risk_multiples"} if family == "profit_ladder" else set())
        if not required <= set(values) or set(values) - permitted:
            raise ValueError(f"{family} has missing or unmodeled fields")
        # Revision-specific quality fields cannot be silently populated from a
        # second, inactive contract.
        if family in {"stop", "profit_ladder"}:
            inactive = ({"minimum_ticker_relative_quality_score", "strict_ticker_relative_quality_gate"}
                        if strategy_revision <= 33 else
                        {"minimum_hold_probability", "minimum_hold_quality_score"})
            if set(values) & inactive:
                raise ValueError(f"{family} has wrong-revision quality fields")
        row = {**identity, **{key: _scalar(values[key], kind, key) if key in values else None
                            for key, kind in spec.items()}}
        result[family] = _seal(row)
    multiples = protection["profit_ladder"]["risk_multiples"]
    if not isinstance(multiples, list) or not multiples or len(multiples) > 64:
        raise ValueError("risk_multiples must be a bounded nonempty list")
    result["risk_multiples"] = [
        _seal({**identity, "ordinal": ordinal,
               "risk_multiple": _scalar(value, "Float64", "risk_multiple")})
        for ordinal, value in enumerate(multiples)
    ]
    profit = {key: value for key, value in result["profit_ladder"].items() if key != "content_hash"}
    profit["risk_multiple_count"] = len(result["risk_multiples"])
    profit["risk_multiple_set_hash"] = sha256(canonical_json(result["risk_multiples"]).encode()).hexdigest()
    result["profit_ladder"] = _seal(profit)
    return result


def restore_sizing_protection_parameters(rows: Mapping[str, Any]) -> dict[str, Any]:
    """Reject missing, extra, reordered or tampered rows; return nested resolver shape."""
    if not isinstance(rows, Mapping) or set(rows) != set(_SPECS) | {"risk_multiples"}:
        raise ValueError("sizing/protection row families incomplete")
    first = rows["sizing"]
    if not isinstance(first, Mapping):
        raise ValueError("invalid sizing row")
    identity = {key: first[key] for key, _ in _IDENTITY}
    values: dict[str, dict[str, Any]] = {}
    for family, spec in _SPECS.items():
        row = rows[family]
        if not isinstance(row, Mapping) or set(row) != {name for name, _ in TABLES[family].columns}:
            raise ValueError(f"invalid {family} columns")
        if any(row[key] != value for key, value in identity.items()):
            raise ValueError("mixed assignment identity")
        if _seal({key: value for key, value in row.items() if key != "content_hash"}) != row:
            raise ValueError(f"tampered {family} row")
        values[family] = {key: row[key] for key in spec if row[key] is not None}
    multiples = rows["risk_multiples"]
    if not isinstance(multiples, list):
        raise ValueError("risk multiple rows must be list")
    values["profit_ladder"]["risk_multiples"] = []
    for ordinal, row in enumerate(multiples):
        if not isinstance(row, Mapping) or set(row) != {name for name, _ in RISK_MULTIPLE_TABLE.columns}:
            raise ValueError("invalid risk multiple columns")
        if any(row[key] != value for key, value in identity.items()) or row["ordinal"] != ordinal:
            raise ValueError("mixed or reordered risk multiple rows")
        if _seal({key: value for key, value in row.items() if key != "content_hash"}) != row:
            raise ValueError("tampered risk multiple row")
        values["profit_ladder"]["risk_multiples"].append(row["risk_multiple"])
    if (rows["profit_ladder"]["risk_multiple_count"] != len(multiples)
            or rows["profit_ladder"]["risk_multiple_set_hash"]
            != sha256(canonical_json(multiples).encode()).hexdigest()):
        raise ValueError("risk multiple set is incomplete or tampered")
    expected = project_sizing_protection_parameters(
        values["sizing"], {key: values[key] for key in _SPECS if key != "sizing"}, **identity,
    )
    if expected != rows:
        raise ValueError("sizing/protection row canonical mismatch")
    return {"sizing": values.pop("sizing"), "protection": values}
