from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_entry_ids import (
    TABLES, project_entry_id_lists, restore_entry_id_lists,
    validate_typed_entry_id_lists,
)
from src.trading_runtime import vwap_resistance_ladder as V
from src.trading_runtime.arte_assignment_state_composite import project_modeled_assignment_state
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_vwap_resistance_ladder import fixture


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def test_ordered_broken_and_cross_known_exact_cold_roundtrip():
    source = {"broken": ["r3", "r2"], "cross_known": ["r1", "r3", "r2"]}
    rows = project_entry_id_lists(source, **IDENTITY)
    class FakeStorage:
        def read(self, table):
            return deepcopy(rows[{TABLES[0].name: "manifest", TABLES[1].name: "broken",
                                  TABLES[2].name: "cross_known"}[table]])
    fake = FakeStorage()
    assert restore_entry_id_lists({
        "manifest": fake.read(TABLES[0].name),
        "broken": fake.read(TABLES[1].name),
        "cross_known": fake.read(TABLES[2].name)}) == source
    assert [row["level_id"] for row in rows["broken"]] == ["r3", "r2"]
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_absent_and_empty_are_distinct_and_full_entry_remains_unmodeled():
    assert restore_entry_id_lists(project_entry_id_lists({}, **IDENTITY)) == {}
    assert restore_entry_id_lists(project_entry_id_lists({"broken": []}, **IDENTITY)) == {
        "broken": []}
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state(
            {**_state(), "vwap_ladder_entry": {"broken": []}}, **KEY)


def test_actual_opt_in_initial_entry_producer_emits_closed_id_lists():
    host, assignment, observation = fixture()
    result = V.evaluate(host, assignment, observation(), assignment.parameters,
                        assignment.state, typed_persistence=True)
    assert result.evaluation.intents[0].action == "enter_long"
    entry = result.state["vwap_ladder_entry"]
    source = validate_typed_entry_id_lists(
        {name: entry[name] for name in ("broken", "cross_known")})
    assert source["broken"] == []
    assert restore_entry_id_lists(project_entry_id_lists(source, **IDENTITY)) == source


@pytest.mark.parametrize("value", [
    {"broken": ["r1", "r1"]}, {"broken": [1]},
    {"cross_known": [""]}, {"cross_known": ("r1",)},
    {"unknown": []},
])
def test_entry_ids_reject_unmodeled_source(value):
    with pytest.raises(ValueError):
        validate_typed_entry_id_lists(value)


def test_entry_ids_reject_reordered_missing_and_mixed_identity_rows():
    rows = project_entry_id_lists({"broken": ["r3", "r2"],
                                   "cross_known": ["r1"]}, **IDENTITY)
    changed = deepcopy(rows)
    changed["broken"].reverse()
    with pytest.raises(ValueError):
        restore_entry_id_lists(changed)
    changed = deepcopy(rows)
    changed["cross_known"].clear()
    with pytest.raises(ValueError):
        restore_entry_id_lists(changed)
    changed = deepcopy(rows)
    changed["broken"][0]["assignment_id"] = "other"
    with pytest.raises(ValueError):
        restore_entry_id_lists(changed)
