from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_parameters import (
    PARAMETER_FAMILIES, project_long_momentum_parameters, restore_long_momentum_parameters,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f", session="2026-09-24")


@pytest.mark.parametrize("revision", range(26, 48))
def test_all_14_resolver_families_roundtrip_per_revision(revision: int) -> None:
    parameters = resolve_long_momentum_parameters(revision=revision)
    assert set(parameters) == PARAMETER_FAMILIES
    rows = project_long_momentum_parameters(parameters, strategy_revision=revision, **KEY)
    assert restore_long_momentum_parameters(rows, strategy_revision=revision, **KEY) == parameters


def test_composite_resolver_override_and_missing_extra_family_fail_closed() -> None:
    parameters = resolve_long_momentum_parameters(
        {"execution": {"tick_size": 0.005},
         "profit_pocket": {"trigger": "favorable_move_pct"}}, revision=47,
    )
    rows = project_long_momentum_parameters(parameters, strategy_revision=47, **KEY)
    assert restore_long_momentum_parameters(rows, strategy_revision=47, **KEY) == parameters
    missing = dict(parameters)
    missing.pop("execution")
    with pytest.raises(ValueError, match="missing or unmodeled"):
        project_long_momentum_parameters(missing, strategy_revision=47, **KEY)
    with pytest.raises(ValueError, match="missing or unmodeled"):
        project_long_momentum_parameters({**parameters, "arbitrary": 1}, strategy_revision=47, **KEY)
    missing_rows = dict(rows)
    missing_rows.pop("entry_rules")
    with pytest.raises(ValueError, match="incomplete"):
        restore_long_momentum_parameters(missing_rows, strategy_revision=47, **KEY)


def test_composite_recovery_rejects_mixed_snapshot_even_with_row_hash_intact() -> None:
    parameters = resolve_long_momentum_parameters(revision=47)
    rows = project_long_momentum_parameters(parameters, strategy_revision=47, **KEY)
    changed = deepcopy(rows)
    changed["execution"] = project_long_momentum_parameters(
        parameters, strategy_revision=47, **{**KEY, "snapshot_id": "5a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f"},
    )["execution"]
    with pytest.raises(ValueError, match="mixed assignment snapshot fence"):
        restore_long_momentum_parameters(changed, strategy_revision=47, **KEY)
