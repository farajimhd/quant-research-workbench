from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_post_move_clock import (
    TABLES, project_post_move_clock, restore_post_move_clock,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_pending_breakout import BASE, _pending
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(clock, present=True):
    return project_post_move_clock(clock, present=present, **{
        key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")})


def _clock():
    pending = _pending(BASE)
    pending["witnessed_at"] = "2026-09-24T14:00:00.000000+00:00"
    return {"session": "2026-09-24", "price": 102.0,
            "consumed_pullbacks": [1790258401.0, 1790258402],
            "consumed_moves": [1790258403.0],
            "pending_breakout": pending}


def test_complete_post_move_clock_exact_order_presence_and_kinds():
    clock = _clock()
    rows = _project(clock)
    assert restore_post_move_clock(rows) == (True, clock)
    assert rows["pullbacks"][1]["pivot_at_int"] == 1790258402
    assert rows["pullbacks"][0]["pivot_at_float"] == 1790258401.0
    assert restore_post_move_clock(_project({})) == (True, {})
    assert restore_post_move_clock(_project(None, present=False)) == (False, {})
    assert restore_post_move_clock(_project({"consumed_moves": []})) == (
        True, {"consumed_moves": []})
    assert all("JSON" not in kind and "Map" not in kind
               for table in TABLES for _, kind in table.columns)


def test_post_move_clock_composite_and_fake_state_cold_readback():
    clock = _clock()
    state = {**_state(), "post_move_entry_clock": clock}
    projected = project_modeled_assignment_state(state, **KEY)
    assert restore_modeled_assignment_state(projected, **KEY) == state
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


@pytest.mark.parametrize("clock", [
    {"unknown": 1},
    {"session": None},
    {"price": True},
    {"price": float("nan")},
    {"consumed_pullbacks": (1.0,)},
    {"consumed_pullbacks": [None]},
    {"consumed_moves": ["move-id"]},
    {"pending_breakout": {"unknown": 1}},
])
def test_post_move_clock_rejects_unmodeled_state(clock):
    with pytest.raises(ValueError):
        _project(clock)


def test_post_move_clock_rejects_reordered_missing_and_orphan_rows():
    rows = _project(_clock())
    changed = deepcopy(rows)
    changed["pullbacks"].reverse()
    with pytest.raises(ValueError):
        restore_post_move_clock(changed)
    changed = deepcopy(rows)
    changed["moves"].clear()
    with pytest.raises(ValueError):
        restore_post_move_clock(changed)
    changed = deepcopy(rows)
    changed["pending"] = None
    with pytest.raises(ValueError):
        restore_post_move_clock(changed)
