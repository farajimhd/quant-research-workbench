from copy import deepcopy

import pytest

from src.trading_runtime.arte_long_momentum_entry_rules import (
    TABLES, project_entry_rules, restore_entry_rules,
)
from src.trading_runtime.strategy_engine import STRATEGY_ID, resolve_long_momentum_parameters


KEY = dict(assignment_id="assignment-1", strategy_id=STRATEGY_ID,
           snapshot_id="4a2c2393-d13d-4ecb-9fae-b5e26ea9ff6f", session="2026-09-24")


@pytest.mark.parametrize("revision", range(26, 48))
def test_entry_rules_nested_ordered_roundtrip_all_revisions(revision: int) -> None:
    source = resolve_long_momentum_parameters(revision=revision)["entry_rules"]
    rows = project_entry_rules(source, strategy_revision=revision, **KEY)
    assert restore_entry_rules(rows) == source
    reversed_rows = {family: list(reversed(value)) for family, value in rows.items()}
    assert restore_entry_rules(reversed_rows) == source
    assert all("storage_policy = 'live_market_ssd'" in table.ddl() for table in TABLES)
    assert all("JSON" not in kind and "Array" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_entry_rules_unknown_nested_and_missing_child_fail_closed() -> None:
    source = resolve_long_momentum_parameters(revision=47)["entry_rules"]
    changed = deepcopy(source)
    changed["confirmation"]["groups"][0]["conditions"][0]["unknown"] = 1
    with pytest.raises(ValueError, match="unmodeled"):
        project_entry_rules(changed, strategy_revision=47, **KEY)
    rows = project_entry_rules(source, strategy_revision=47, **KEY)
    missing = {**rows, "condition": rows["condition"][:-1]}
    with pytest.raises(ValueError, match="invalid|hash|count|order"):
        restore_entry_rules(missing)
