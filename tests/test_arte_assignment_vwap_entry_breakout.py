from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_entry_breakout import (
    TABLES, project_entry_breakout_setup, restore_entry_breakout_setup,
    validate_entry_breakout_setup,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state,
)
from tests.test_arte_assignment_pending_breakout import BASE, _pending
from tests.test_arte_assignment_state_composite import KEY, _state


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def test_entry_breakout_setup_distinct_typed_family_cold_readback():
    value = _pending({**BASE, "unified_level_id": "zone:r1",
                      "members": ["r1", "r2"], "encountered": True,
                      "seen_below": True, "grouping_threshold": 0.25})
    normalized = validate_entry_breakout_setup(value)
    rows = project_entry_breakout_setup(value, **IDENTITY)

    class FakeStorage:
        def read(self, table):
            key = {TABLES[0].name: "setup", TABLES[1].name: "parent",
                   TABLES[2].name: "members"}[table]
            return deepcopy(rows[key])

    fake = FakeStorage()
    assert restore_entry_breakout_setup({
        "setup": fake.read(TABLES[0].name),
        "parent": fake.read(TABLES[1].name),
        "members": fake.read(TABLES[2].name)}) == normalized
    assert len({table.name for table in TABLES}) == 3
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_none_is_distinct_and_full_entry_stays_unmodeled():
    rows = project_entry_breakout_setup(None, **IDENTITY)
    assert restore_entry_breakout_setup(rows) is None
    assert rows["parent"] == rows["members"] == []
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state(
            {**_state(), "vwap_ladder_entry": {"breakout_setup": None}}, **KEY)


@pytest.mark.parametrize("bad", [
    {"anchor": {**BASE, "other": True}},
    {"target_price": 0.0},
    {"witnessed_at": "2026-09-24T10:00:00-04:00"},
])
def test_typed_entry_breakout_rejects_invalid_source(bad):
    with pytest.raises(ValueError):
        project_entry_breakout_setup({**_pending(BASE), **bad}, **IDENTITY)


def test_entry_breakout_corruption_and_mixed_identity_rejected():
    rows = project_entry_breakout_setup(_pending(BASE), **IDENTITY)
    changed = deepcopy(rows)
    changed["parent"][0]["anchor_id"] = "other"
    with pytest.raises(ValueError):
        restore_entry_breakout_setup(changed)
    changed = deepcopy(rows)
    changed["setup"]["snapshot_id"] = "02e19bda-3786-4ee2-babe-05ad05bfc10a"
    with pytest.raises(ValueError):
        restore_entry_breakout_setup(changed)
