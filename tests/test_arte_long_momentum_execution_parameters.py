from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_execution_parameters import (
    TABLE, project_execution_parameters, restore_execution_parameters,
)
from src.trading_runtime.strategy_engine import (
    STRATEGY_ID, resolve_long_momentum_parameters,
)


SNAPSHOT = "4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"


@pytest.mark.parametrize("revision", range(26, 48))
def test_all_registered_revision_execution_defaults_roundtrip(revision: int) -> None:
    execution = resolve_long_momentum_parameters(revision=revision)["execution"]
    row = project_execution_parameters(
        execution, assignment_id="assignment-1", strategy_id=STRATEGY_ID,
        strategy_revision=revision, snapshot_id=SNAPSHOT, session="2026-09-24",
    )
    assert restore_execution_parameters(row) == execution
    assert "storage_policy = 'live_market_ssd'" in TABLE.ddl()
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for _, kind in TABLE.columns)


def test_custom_resolved_execution_and_cold_corruption() -> None:
    execution = resolve_long_momentum_parameters(
        {"execution": {"entry_urgency": "patient", "tick_size": 0.005}}, revision=47,
    )["execution"]
    row = project_execution_parameters(
        execution, assignment_id="a", strategy_id=STRATEGY_ID,
        strategy_revision=47, snapshot_id=SNAPSHOT, session="2026-09-24",
    )
    assert restore_execution_parameters(row) == execution
    changed = deepcopy(row)
    changed["tick_size"] = 0.01
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_execution_parameters(changed)


@pytest.mark.parametrize("change", [
    {"extra": 1}, {"tick_size": 0}, {"tick_size": float("nan")},
    {"entry_urgency": "unmodeled"},
])
def test_execution_family_rejects_unmodeled_and_invalid_values(change: dict) -> None:
    execution = resolve_long_momentum_parameters(revision=47)["execution"]
    with pytest.raises(ValueError):
        project_execution_parameters(
            {**execution, **change}, assignment_id="a", strategy_id=STRATEGY_ID,
            strategy_revision=47, snapshot_id=SNAPSHOT, session="2026-09-24",
        )
