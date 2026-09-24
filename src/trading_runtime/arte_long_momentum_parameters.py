"""Inactive complete typed Long Momentum parameter composition v1.

Every resolver family must project and recover before this envelope is usable.
This module does not publish, create tables or activate the live route.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.trading_runtime.arte_long_momentum_entry_confirm_parameters import (
    project_entry_confirm_parameters, restore_entry_confirm_parameters,
)
from src.trading_runtime.arte_long_momentum_entry_rules import project_entry_rules, restore_entry_rules
from src.trading_runtime.arte_long_momentum_execution_parameters import (
    project_execution_parameters, restore_execution_parameters,
)
from src.trading_runtime.arte_long_momentum_exit_parameters import (
    project_exit_parameters, restore_exit_parameters,
)
from src.trading_runtime.arte_long_momentum_lifecycle_parameters import (
    project_lifecycle_parameters, restore_lifecycle_parameters,
)
from src.trading_runtime.arte_long_momentum_sizing_protection_parameters import (
    project_sizing_protection_parameters, restore_sizing_protection_parameters,
)
from src.trading_runtime.arte_long_momentum_structure_parameters import (
    project_structure_parameters, restore_structure_parameters,
)


PARAMETER_FAMILIES = frozenset({
    "liquidity_admission", "entry_momentum_confirmation", "entry_candle_confirmation",
    "sizing", "protection", "execution", "add", "reentry", "profit_pocket",
    "final_exit", "exit_routes", "momentum_management", "entry_rules", "structural_entry",
})
_ROW_FAMILIES = frozenset({
    "entry_confirm", "sizing_protection", "execution", "lifecycle",
    "exit", "structure", "entry_rules",
})
_IDENTITY = ("assignment_id", "strategy_id", "strategy_revision", "snapshot_id", "session")


def project_long_momentum_parameters(
    parameters: Mapping[str, Any], *, assignment_id: str, strategy_id: str,
    strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Reject partial or extra resolver output, then project all 14 families."""
    if not isinstance(parameters, Mapping) or set(parameters) != PARAMETER_FAMILIES:
        raise ValueError("Long Momentum parameters have missing or unmodeled families")
    identity = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                    strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    return {
        "entry_confirm": project_entry_confirm_parameters(
            {key: parameters[key] for key in (
                "liquidity_admission", "entry_momentum_confirmation", "entry_candle_confirmation")},
            **identity,
        ),
        "sizing_protection": project_sizing_protection_parameters(
            parameters["sizing"], parameters["protection"], **identity,
        ),
        "execution": project_execution_parameters(parameters["execution"], **identity),
        "lifecycle": project_lifecycle_parameters(
            {key: parameters[key] for key in ("add", "reentry", "profit_pocket")}, **identity,
        ),
        "exit": project_exit_parameters(parameters["final_exit"], parameters["exit_routes"], **identity),
        "structure": project_structure_parameters(
            {key: parameters[key] for key in ("momentum_management", "structural_entry")}, **identity,
        ),
        "entry_rules": project_entry_rules(parameters["entry_rules"], **identity),
    }


def _assert_fence(value: Any, identity: Mapping[str, Any]) -> None:
    if isinstance(value, list):
        for row in value:
            _assert_fence(row, identity)
    elif isinstance(value, Mapping):
        if "assignment_id" in value:
            if any(value.get(key) != identity[key] for key in _IDENTITY):
                raise ValueError("Long Momentum parameter row has mixed assignment snapshot fence")
        else:
            for child in value.values():
                _assert_fence(child, identity)
    else:
        raise ValueError("Long Momentum parameter rows have invalid shape")


def restore_long_momentum_parameters(
    rows: Mapping[str, Any], *, assignment_id: str, strategy_id: str,
    strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, Any]:
    """Verify a single pinned snapshot and recover every resolver family."""
    if not isinstance(rows, Mapping) or set(rows) != _ROW_FAMILIES:
        raise ValueError("Long Momentum typed parameter row families are incomplete")
    identity = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                    strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    _assert_fence(rows, identity)
    result: dict[str, Any] = {}
    result.update(restore_entry_confirm_parameters(rows["entry_confirm"]))
    restored_sizing = restore_sizing_protection_parameters(rows["sizing_protection"])
    result.update(restored_sizing)
    result["execution"] = restore_execution_parameters(rows["execution"])
    result.update(restore_lifecycle_parameters(rows["lifecycle"]))
    result["final_exit"], result["exit_routes"] = restore_exit_parameters(rows["exit"])
    result.update(restore_structure_parameters(rows["structure"]))
    result["entry_rules"] = restore_entry_rules(rows["entry_rules"])
    if set(result) != PARAMETER_FAMILIES:
        raise ValueError("Long Momentum typed parameter recovery is incomplete")
    # Reprojection checks each named family against the pinned identity,
    # including revisions whose optional scalar columns differ.
    project_long_momentum_parameters(result, **identity)
    return result
