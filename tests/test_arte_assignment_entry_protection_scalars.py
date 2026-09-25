from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_entry_protection_scalars import (
    TABLE, project_entry_protection_scalars, restore_entry_protection_scalars,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(values):
    return project_entry_protection_scalars(
        values, assignment_id=KEY["assignment_id"], revision=KEY["revision"],
        snapshot_id=KEY["snapshot_id"], session=KEY["session"])


def test_entry_protection_exact_named_scalar_roundtrip():
    values = dict(breakout_level=101.0, breakout_buffer_bps=5.0,
                  entry_reference_price=101.2, initial_stop=99.0,
                  active_stop=99.0, trailing_amount=1.25,
                  high_water_price=101.2, low_water_price=101.2)
    row = _project(values)
    assert restore_entry_protection_scalars(row) == values
    assert restore_entry_protection_scalars(_project({"entry_reference_price": None})) == {
        "entry_reference_price": None}
    assert restore_entry_protection_scalars(_project({})) == {}
    assert all("Map" not in kind and "JSON" not in kind for _, kind in TABLE.columns)


def test_entry_protection_composite_and_state_snapshot_roundtrip():
    source = {**_state(), "entry_reference_price": 101.2,
              "initial_stop": 99.0, "active_stop": 99.0,
              "high_water_price": 102.0, "low_water_price": 100.0}
    rows = project_modeled_assignment_state(source, **KEY)
    assert restore_modeled_assignment_state(rows, **KEY) == source
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert TABLE in STATE_TABLES
    flat, commit = project_state_snapshot(source, **KEY)
    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])
    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == source


@pytest.mark.parametrize("values", [
    {"unknown": 1.0}, {"initial_stop": 99},
    {"active_stop": float("nan")}, {"high_water_price": float("inf")},
    {"low_water_price": False},
])
def test_entry_protection_rejects_unmodeled_or_nonfloat(values):
    with pytest.raises(ValueError):
        _project(values)


def test_entry_protection_rejects_corrupt_presence_or_hash():
    row = _project({"initial_stop": None})
    with pytest.raises(ValueError):
        restore_entry_protection_scalars({**row, "initial_stop_present": False})
    with pytest.raises(ValueError):
        restore_entry_protection_scalars({**row, "initial_stop": 99.0})
