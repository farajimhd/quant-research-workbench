from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_position_entry_identity import (
    TABLES, project_position_entry_identity, restore_position_entry_identity,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(state):
    return project_position_entry_identity(state, **{key: KEY[key] for key in
                                                     ("assignment_id", "revision", "snapshot_id", "session")})


def test_position_entry_order_presence_and_tranche_roundtrip():
    state = {"position_entry_level_ids": ["r4", "r3"], "position_entry_tranches": 2}
    rows = _project(state)
    assert restore_position_entry_identity(rows) == state
    assert restore_position_entry_identity(_project({})) == {}
    assert restore_position_entry_identity(_project({"position_entry_level_ids": []})) == {
        "position_entry_level_ids": []}
    assert rows["levels"][0]["ordinal"] == 0
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_position_entry_composite_and_fake_cold_readback():
    state = {**_state(), "position_entry_level_ids": ["r4", "r3"],
             "position_entry_tranches": 2}
    rows = project_modeled_assignment_state(state, **KEY)
    assert restore_modeled_assignment_state(rows, **KEY) == state
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert all(table in STATE_TABLES for table in TABLES)
    flat, commit = project_state_snapshot(state, **KEY)

    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])

    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == state


@pytest.mark.parametrize("state", [
    {"position_entry_level_ids": ["r4", "r4"]},
    {"position_entry_level_ids": [""]},
    {"position_entry_level_ids": ("r4",)},
    {"position_entry_level_ids": [1]},
    {"position_entry_tranches": True},
    {"position_entry_tranches": -1},
    {"position_entry_tranches": 65536},
    {"unknown": 1},
])
def test_position_entry_rejects_unmodeled_or_invalid_input(state):
    with pytest.raises(ValueError):
        _project(state)


def test_position_entry_rejects_reordered_missing_or_corrupt_rows():
    rows = _project({"position_entry_level_ids": ["r4", "r3"],
                     "position_entry_tranches": 2})
    changed = deepcopy(rows)
    changed["levels"].reverse()
    with pytest.raises(ValueError):
        restore_position_entry_identity(changed)
    changed = deepcopy(rows)
    changed["levels"].pop()
    with pytest.raises(ValueError):
        restore_position_entry_identity(changed)
    changed = deepcopy(rows)
    changed["manifest"]["tranches"] = 3
    with pytest.raises(ValueError):
        restore_position_entry_identity(changed)
