from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_structure_parameters import (
    TABLES, project_structure_parameters, restore_structure_parameters,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f", session="2026-09-24")


def selected(revision: int) -> dict:
    parameters = resolve_long_momentum_parameters(revision=revision)
    return {key: parameters[key] for key in ("structural_entry", "momentum_management")}


@pytest.mark.parametrize("revision", range(26, 48))
def test_named_structure_families_roundtrip_all_revisions(revision: int) -> None:
    source = selected(revision)
    rows = project_structure_parameters(source, strategy_revision=revision, **KEY)
    assert restore_structure_parameters(rows) == source
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_structure_nested_unknown_and_readback_corruption_fail_closed() -> None:
    source = selected(47)
    changed = deepcopy(source)
    changed["momentum_management"]["macd_backstop"]["unmodeled"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_structure_parameters(changed, strategy_revision=47, **KEY)
    rows = project_structure_parameters(source, strategy_revision=47, **KEY)
    altered = deepcopy(rows)
    altered["structural_entry"]["acceptance_hold_ms"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_structure_parameters(altered)
