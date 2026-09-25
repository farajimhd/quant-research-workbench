from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_lifecycle_counters import (
    TABLE, project_lifecycle_counters, restore_lifecycle_counters,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(values):
    return project_lifecycle_counters(
        values, assignment_id=KEY["assignment_id"], revision=KEY["revision"],
        snapshot_id=KEY["snapshot_id"], session=KEY["session"])


def test_lifecycle_counters_absent_zero_and_nonzero_exact_roundtrip():
    assert restore_lifecycle_counters(_project({})) == {}
    values = {"entries": 0, "adds": 2, "reentries": 1, "profit_takes": 3}
    row = _project(values)
    assert restore_lifecycle_counters(row) == values
    assert row["entries_present"] and row["entries"] == 0
    assert all("Map" not in kind and "JSON" not in kind for _, kind in TABLE.columns)


def test_composite_and_state_storage_include_closed_counter_family():
    source = {**_state(), "entries": 1, "adds": 0, "reentries": 0,
              "profit_takes": 2}
    rows = project_modeled_assignment_state(source, **KEY)
    assert restore_modeled_assignment_state(rows, **KEY) == source
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert TABLE in STATE_TABLES
    flat, commit = project_state_snapshot(source, **KEY)
    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name
                            else flat[table])
    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == source


@pytest.mark.parametrize("values", [
    {"unknown": 1}, {"entries": True}, {"adds": -1},
    {"reentries": 1.0}, {"profit_takes": 2 ** 64},
])
def test_lifecycle_counters_fail_closed(values):
    with pytest.raises(ValueError):
        _project(values)


def test_lifecycle_counters_reject_mutated_presence_and_hash():
    row = _project({"entries": 0})
    with pytest.raises(ValueError):
        restore_lifecycle_counters({**row, "entries_present": False})
    with pytest.raises(ValueError):
        restore_lifecycle_counters({**row, "entries": 1})
