from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_exit_parameters import (
    TABLES, project_exit_parameters, restore_exit_parameters,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f", session="2026-09-24")


@pytest.mark.parametrize("revision", range(26, 48))
def test_resolved_exit_families_roundtrip_all_revisions(revision: int) -> None:
    parameters = resolve_long_momentum_parameters(revision=revision)
    rows = project_exit_parameters(parameters["final_exit"], parameters["exit_routes"],
                                   strategy_revision=revision, **KEY)
    assert restore_exit_parameters(rows) == (parameters["final_exit"], parameters["exit_routes"])
    reversed_rows = {**rows, "exit_route": list(reversed(rows["exit_route"]))}
    assert restore_exit_parameters(reversed_rows) == (parameters["final_exit"], parameters["exit_routes"])
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_exit_route_rejects_unknown_nested_settings_and_missing_rows() -> None:
    parameters = resolve_long_momentum_parameters(revision=47)
    bad = deepcopy(parameters["exit_routes"])
    bad[2]["settings"]["unknown"] = 1
    with pytest.raises(ValueError, match="settings"):
        project_exit_parameters(parameters["final_exit"], bad, strategy_revision=47, **KEY)
    rows = project_exit_parameters(parameters["final_exit"], parameters["exit_routes"],
                                   strategy_revision=47, **KEY)
    missing = {**rows, "exit_route": rows["exit_route"][:-1]}
    with pytest.raises(ValueError):
        restore_exit_parameters(missing)
    changed = deepcopy(rows)
    changed["exit_route"][1]["enabled"] = 0
    with pytest.raises(ValueError, match="hash|fence"):
        restore_exit_parameters(changed)


def test_custom_ordered_route_values_roundtrip() -> None:
    parameters = resolve_long_momentum_parameters(revision=47)
    routes = deepcopy(parameters["exit_routes"])
    routes[1]["name"] = "Operator review"
    routes[1]["enabled"] = False
    routes[2]["settings"]["qmd_score"] = -0.45
    rows = project_exit_parameters(parameters["final_exit"], routes,
                                   strategy_revision=47, **KEY)
    assert restore_exit_parameters(rows) == (parameters["final_exit"], routes)
