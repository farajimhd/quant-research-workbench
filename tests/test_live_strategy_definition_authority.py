from __future__ import annotations

from copy import deepcopy

import pytest

from src.backend.live_strategy_definition_authority import (
    DEFINITION, ENABLE_CHANGE, _installed, project_enable_change,
    project_installed_definition, recover_installed_definition,
)
from src.trading_runtime.strategy_engine import (
    HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION,
)


def _saved(revision: int) -> dict:
    return {**_installed(STRATEGY_ID, revision),
            "created_at": "2026-09-24T14:00:00+00:00"}


@pytest.mark.parametrize("revision", (*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
def test_all_installed_revisions_recover_from_immutable_definition_and_first_change(
        revision: int) -> None:
    saved = _saved(revision)
    definition = project_installed_definition(saved)
    first = project_enable_change(definition, change_sequence=1,
                                  enabled=True, changed_at=saved["created_at"])
    recovered = recover_installed_definition(
        [definition], [first], expected_change_sequence=1,
        expected_change_hash=first["content_hash"])
    assert recovered == {**saved, "created_at": "2026-09-24T14:00:00.000000+00:00",
                         "taxonomy": saved["config"]["taxonomy"]}


def test_multiple_same_day_enable_changes_are_ordered_and_exact() -> None:
    definition = project_installed_definition(_saved(STRATEGY_REVISION))
    changes = []
    prior = None
    for sequence, enabled in enumerate((True, False, True), 1):
        row = project_enable_change(
            definition, change_sequence=sequence, enabled=enabled,
            changed_at=f"2026-09-24T14:00:0{sequence - 1}+00:00",
            previous_change_hash=prior)
        changes.append(row)
        prior = row["content_hash"]
    assert recover_installed_definition(
        [definition], list(reversed(changes)), expected_change_sequence=3,
        expected_change_hash=prior)["enabled"] is True
    with pytest.raises(ValueError, match="incomplete"):
        recover_installed_definition([definition], changes[:2],
                                     expected_change_sequence=3, expected_change_hash=prior)
    with pytest.raises(ValueError, match="duplicate or gapped"):
        recover_installed_definition([definition], [changes[0], changes[0], changes[2]],
                                     expected_change_sequence=3, expected_change_hash=prior)
    with pytest.raises(ValueError, match="head differs"):
        recover_installed_definition([definition], changes,
                                     expected_change_sequence=3,
                                     expected_change_hash="0" * 64)
    with pytest.raises(ValueError, match="duplicated"):
        recover_installed_definition([definition, definition], changes,
                                     expected_change_sequence=3, expected_change_hash=prior)


def test_config_drift_and_corrupt_change_fail_closed() -> None:
    saved = _saved(STRATEGY_REVISION)
    changed = deepcopy(saved)
    changed["config"]["direction"] = "modified"
    with pytest.raises(ValueError, match="config differs"):
        project_installed_definition(changed)
    definition = project_installed_definition(saved)
    first = project_enable_change(definition, change_sequence=1,
                                  enabled=True, changed_at=saved["created_at"])
    with pytest.raises(ValueError, match="row differs"):
        recover_installed_definition([definition], [{**first, "enabled": False}],
                                     expected_change_sequence=1,
                                     expected_change_hash=first["content_hash"])


def test_route_shaped_definition_and_normalized_tables() -> None:
    installed = _saved(STRATEGY_REVISION)
    saved = {key: deepcopy(installed[key]) for key in (
        "strategy_id", "revision", "name", "implementation", "automatic",
        "enabled", "config", "created_at")}
    saved["taxonomy"] = deepcopy(saved["config"]["taxonomy"])
    saved["created_at"] = "2026-09-24T10:00:00-04:00"
    row = project_installed_definition(saved)
    assert row["created_at"] == "2026-09-24T14:00:00.000000+00:00"
    assert "enabled" not in row and "session_key" not in row
    for table in (DEFINITION, ENABLE_CHANGE):
        ddl = table.ddl()
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert not any(kind in ddl for kind in ("JSON", "Object", "Map(", "Blob"))
