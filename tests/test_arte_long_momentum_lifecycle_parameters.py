from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_lifecycle_parameters import (
    TABLES, project_lifecycle_parameters, restore_lifecycle_parameters,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f", session="2026-09-24")
FAMILIES = ("add", "reentry", "profit_pocket")


def selected(revision: int) -> dict:
    parameters = resolve_long_momentum_parameters(revision=revision)
    return {family: parameters[family] for family in FAMILIES}


@pytest.mark.parametrize("revision", range(26, 48))
def test_lifecycle_family_roundtrip_all_revisions(revision: int) -> None:
    source = selected(revision)
    rows = project_lifecycle_parameters(source, strategy_revision=revision, **KEY)
    assert restore_lifecycle_parameters(rows) == source
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_lifecycle_nested_unknowns_and_cold_corruption_fail_closed() -> None:
    source = selected(47)
    changed = deepcopy(source)
    changed["reentry"]["pullback_reclaim"]["unknown"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_lifecycle_parameters(changed, strategy_revision=47, **KEY)
    rows = project_lifecycle_parameters(source, strategy_revision=47, **KEY)
    missing = {key: value for key, value in rows.items() if key != "add"}
    with pytest.raises(ValueError, match="incomplete"):
        restore_lifecycle_parameters(missing)
    altered = deepcopy(rows)
    altered["profit_pocket"]["quantity_fraction"] = 0.5
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_lifecycle_parameters(altered)


def test_custom_typed_scalar_values_roundtrip() -> None:
    source = selected(26)
    source["add"]["maximum_adds"] = 3
    source["reentry"]["target_replenishment"]["support_buffer_bps"] = 11.25
    source["profit_pocket"]["trigger"] = "favorable_move_pct"
    rows = project_lifecycle_parameters(source, strategy_revision=26, **KEY)
    assert restore_lifecycle_parameters(rows) == source
