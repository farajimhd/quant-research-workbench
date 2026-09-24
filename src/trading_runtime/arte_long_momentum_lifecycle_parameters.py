"""Inactive named typed add, reentry and profit-pocket parameter families v1."""
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
_KEY = (("assignment_id", "String"), ("strategy_id", "String"),
        ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"), ("session", "Date"))
_ADD = (("enabled", "UInt8"), ("trigger", "String"), ("maximum_adds", "UInt32"))
_REENTRY = (
    ("enabled", "UInt8"), ("cooldown_ms", "UInt64"),
    ("maximum_attempts", "UInt32"), ("unlimited_attempts", "UInt8"),
    ("require_new_confirmation", "UInt8"),
    ("pullback_reclaim_enabled", "UInt8"),
    ("pullback_reclaim_minimum_pullback_atr_multiple", "Float64"),
    ("pullback_reclaim_minimum_pullback_bps", "Float64"),
    ("target_replenishment_enabled", "UInt8"),
    ("target_replenishment_minimum_pullback_atr_multiple", "Float64"),
    ("target_replenishment_minimum_pullback_bps", "Float64"),
    ("target_replenishment_support_buffer_bps", "Float64"),
)
_POCKET = (
    ("enabled", "UInt8"), ("trigger", "String"),
    ("minimum_gain_pct", "Float64"), ("quantity_fraction", "Float64"),
    ("minimum_remaining_quantity", "Float64"),
    ("acceleration_slowdown_threshold", "Float64"),
    ("volatility_multiple", "Float64"),
)
TABLES = tuple(
    TableContract(f"trading_assignment_long_momentum_{family}_parameters_v1",
                  _KEY + columns + (("content_hash", "FixedString(64)"),),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id")
    for family, columns in (("add", _ADD), ("reentry", _REENTRY), ("profit_pocket", _POCKET))
)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def _closed(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} has missing or unmodeled fields")
    return value


def _bool(value: Any, label: str) -> int:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return int(value)


def _uint(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be nonnegative integer")
    return value


def _float(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def project_lifecycle_parameters(
    parameters: Mapping[str, Any], *, assignment_id: str, strategy_id: str,
    strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, dict[str, Any]]:
    """Project exactly these three complete resolved parameter subfamilies."""
    if (not assignment_id or strategy_id != STRATEGY_ID or type(strategy_revision) is not int
            or strategy_revision not in _REVISIONS):
        raise ValueError("lifecycle parameter strategy identity/revision is unsupported")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("lifecycle parameter session is invalid")
    source = _closed(parameters, {"add", "reentry", "profit_pocket"}, "lifecycle parameters")
    add = _closed(source["add"], {name for name, _ in _ADD}, "add parameters")
    reentry = _closed(source["reentry"], {
        "enabled", "cooldown_ms", "maximum_attempts", "unlimited_attempts",
        "require_new_confirmation", "pullback_reclaim", "target_replenishment",
    }, "reentry parameters")
    reclaim = _closed(reentry["pullback_reclaim"], {
        "enabled", "minimum_pullback_atr_multiple", "minimum_pullback_bps",
    }, "pullback reclaim")
    replenish = _closed(reentry["target_replenishment"], {
        "enabled", "minimum_pullback_atr_multiple", "minimum_pullback_bps", "support_buffer_bps",
    }, "target replenishment")
    pocket = _closed(source["profit_pocket"], {name for name, _ in _POCKET}, "profit pocket")
    if not isinstance(add["trigger"], str) or not add["trigger"]:
        raise ValueError("add trigger is invalid")
    if pocket["trigger"] not in {"acceleration_slowdown", "favorable_move_pct", "volatility_multiple"}:
        raise ValueError("profit pocket trigger is unsupported")
    common = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                  strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    return {
        "add": _seal({**common, "enabled": _bool(add["enabled"], "add enabled"),
                      "trigger": add["trigger"], "maximum_adds": _uint(add["maximum_adds"], "maximum adds")}),
        "reentry": _seal({
            **common, "enabled": _bool(reentry["enabled"], "reentry enabled"),
            "cooldown_ms": _uint(reentry["cooldown_ms"], "reentry cooldown"),
            "maximum_attempts": _uint(reentry["maximum_attempts"], "maximum attempts"),
            "unlimited_attempts": _bool(reentry["unlimited_attempts"], "unlimited attempts"),
            "require_new_confirmation": _bool(reentry["require_new_confirmation"], "new confirmation"),
            "pullback_reclaim_enabled": _bool(reclaim["enabled"], "reclaim enabled"),
            "pullback_reclaim_minimum_pullback_atr_multiple": _float(reclaim["minimum_pullback_atr_multiple"], "reclaim ATR"),
            "pullback_reclaim_minimum_pullback_bps": _float(reclaim["minimum_pullback_bps"], "reclaim bps"),
            "target_replenishment_enabled": _bool(replenish["enabled"], "replenishment enabled"),
            "target_replenishment_minimum_pullback_atr_multiple": _float(replenish["minimum_pullback_atr_multiple"], "replenishment ATR"),
            "target_replenishment_minimum_pullback_bps": _float(replenish["minimum_pullback_bps"], "replenishment bps"),
            "target_replenishment_support_buffer_bps": _float(replenish["support_buffer_bps"], "replenishment support bps"),
        }),
        "profit_pocket": _seal({
            **common, "enabled": _bool(pocket["enabled"], "pocket enabled"),
            "trigger": pocket["trigger"],
            "minimum_gain_pct": _float(pocket["minimum_gain_pct"], "pocket minimum gain"),
            "quantity_fraction": _float(pocket["quantity_fraction"], "pocket fraction"),
            "minimum_remaining_quantity": _float(pocket["minimum_remaining_quantity"], "pocket remaining"),
            "acceleration_slowdown_threshold": _float(pocket["acceleration_slowdown_threshold"], "pocket slowdown"),
            "volatility_multiple": _float(pocket["volatility_multiple"], "pocket volatility"),
        }),
    }


def restore_lifecycle_parameters(rows: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Cold-read all three same-snapshot rows and verify exact content."""
    if set(rows) != {"add", "reentry", "profit_pocket"}:
        raise ValueError("lifecycle parameter families are incomplete")
    for row, table in zip((rows["add"], rows["reentry"], rows["profit_pocket"]), TABLES):
        if set(row) != {name for name, _ in table.columns}:
            raise ValueError("lifecycle parameter row columns differ")
    first = rows["add"]
    common = {key: first[key] for key, _ in _KEY}
    if any(any(row[key] != value for key, value in common.items()) for row in rows.values()):
        raise ValueError("lifecycle parameter snapshot fence differs")
    add, reentry, pocket = rows["add"], rows["reentry"], rows["profit_pocket"]
    result = {
        "add": {"enabled": bool(add["enabled"]), "trigger": add["trigger"],
                "maximum_adds": add["maximum_adds"]},
        "reentry": {
            "enabled": bool(reentry["enabled"]), "cooldown_ms": reentry["cooldown_ms"],
            "maximum_attempts": reentry["maximum_attempts"],
            "unlimited_attempts": bool(reentry["unlimited_attempts"]),
            "require_new_confirmation": bool(reentry["require_new_confirmation"]),
            "pullback_reclaim": {
                "enabled": bool(reentry["pullback_reclaim_enabled"]),
                "minimum_pullback_atr_multiple": reentry["pullback_reclaim_minimum_pullback_atr_multiple"],
                "minimum_pullback_bps": reentry["pullback_reclaim_minimum_pullback_bps"],
            },
            "target_replenishment": {
                "enabled": bool(reentry["target_replenishment_enabled"]),
                "minimum_pullback_atr_multiple": reentry["target_replenishment_minimum_pullback_atr_multiple"],
                "minimum_pullback_bps": reentry["target_replenishment_minimum_pullback_bps"],
                "support_buffer_bps": reentry["target_replenishment_support_buffer_bps"],
            },
        },
        "profit_pocket": {
            "enabled": bool(pocket["enabled"]), "trigger": pocket["trigger"],
            "minimum_gain_pct": pocket["minimum_gain_pct"],
            "quantity_fraction": pocket["quantity_fraction"],
            "minimum_remaining_quantity": pocket["minimum_remaining_quantity"],
            "acceleration_slowdown_threshold": pocket["acceleration_slowdown_threshold"],
            "volatility_multiple": pocket["volatility_multiple"],
        },
    }
    if project_lifecycle_parameters(result, **common) != rows:
        raise ValueError("lifecycle parameter content hash mismatch")
    return result
