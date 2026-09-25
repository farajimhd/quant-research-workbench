from copy import deepcopy

import pytest

from src.trading_runtime import vwap_resistance_ladder as V
from src.trading_runtime.arte_assignment_vwap_entry import (
    ROW_KEYS, project_vwap_entry, restore_vwap_entry,
)
from src.trading_runtime.arte_assignment_pending_breakout import validate_pending_breakout
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_pending_breakout import BASE, _pending
from tests.test_arte_assignment_vwap_pullback_move import _move
from tests.test_arte_assignment_vwap_retest_anchor import _witness
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_vwap_resistance_ladder import fixture


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def _initial():
    host, assignment, observation = fixture()
    result = V.evaluate(host, assignment, observation(), assignment.parameters,
                        assignment.state, typed_persistence=True)
    return deepcopy(result.state["vwap_ladder_entry"])


def _breakout():
    entry = _initial()
    entry.update(entry_kind="post_move_breakout", swing=None, retest_anchor=None,
                 breakout_setup=validate_pending_breakout(_pending(BASE)),
                 pullback_move=None)
    return entry


def _pullback():
    entry = _initial()
    retest = _witness()
    entry.update(entry_kind="post_move_pullback", swing=deepcopy(retest["swing"]),
                 retest_anchor=retest, breakout_setup=None, pullback_move=_move())
    return entry


@pytest.mark.parametrize("source", [_initial, _breakout, _pullback])
def test_all_vwap_entry_kinds_exact_composite_and_fake_state_cold_readback(source):
    entry = source()
    projected = project_vwap_entry(entry, **IDENTITY)
    assert set(projected) == ROW_KEYS
    assert restore_vwap_entry(projected, **IDENTITY) == entry
    if entry["retest_anchor"] is not None:
        assert projected["swing"] is None  # The nested retest already owns it.
    state = {**_state(), "vwap_ladder_entry": entry}
    composite = project_modeled_assignment_state(state, **KEY)
    assert restore_modeled_assignment_state(composite, **KEY) == state
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    flat, commit = project_state_snapshot(state, **KEY)
    class FakeStorage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])
    assert recover_state_snapshot(FakeStorage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == state


def test_vwap_entry_rejects_unmodeled_top_level_and_unlinked_evidence():
    entry = _initial()
    with pytest.raises(ValueError, match="unmodeled"):
        project_vwap_entry({**entry, "unknown": 1}, **IDENTITY)
    changed = deepcopy(entry)
    changed.pop("pullback_move")
    with pytest.raises(ValueError, match="missing"):
        project_vwap_entry(changed, **IDENTITY)
    changed = _pullback()
    changed["swing"]["price"] = 3.91
    with pytest.raises(ValueError, match="differ"):
        project_vwap_entry(changed, **IDENTITY)
    changed = _breakout()
    changed["entry_kind"] = "initial"
    with pytest.raises(ValueError):
        project_vwap_entry(changed, **IDENTITY)


def test_vwap_entry_rejects_missing_child_and_redundant_swing_rows():
    projected = project_vwap_entry(_pullback(), **IDENTITY)
    changed = deepcopy(projected)
    changed["retest_swing"] = None
    with pytest.raises(ValueError):
        restore_vwap_entry(changed, **IDENTITY)
    changed = deepcopy(projected)
    changed["swing"] = changed["retest_swing"]
    with pytest.raises(ValueError, match="redundantly"):
        restore_vwap_entry(changed, **IDENTITY)
    changed = deepcopy(projected)
    changed["ids_manifest"]["assignment_id"] = "other"
    with pytest.raises(ValueError):
        restore_vwap_entry(changed, **IDENTITY)
