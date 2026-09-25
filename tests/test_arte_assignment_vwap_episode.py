from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_episode import (
    TABLE, project_vwap_episode, restore_vwap_episode,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state


def _project(value, present=True):
    return project_vwap_episode(value, present=present, **{
        key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")})


def test_vwap_episode_closed_named_scalar_roundtrip():
    episode = {"session": "2026-09-24", "sample_at": 1790262000.0,
               "bullish": True, "used": False, "started_at": 1790262000.0}
    assert restore_vwap_episode(_project(episode)) == (True, episode)
    assert restore_vwap_episode(_project({})) == (True, {})
    assert restore_vwap_episode(_project(None, present=False)) == (False, {})
    assert all("JSON" not in kind and "Map" not in kind for _, kind in TABLE.columns)


def test_vwap_episode_composite_and_fake_cold_readback():
    episode = {"session": "2026-09-24", "sample_at": 1790262000.0,
               "bullish": True, "used": False, "started_at": 1790262000.0}
    state = {**_state(), "vwap_ladder_episode": episode}
    rows = project_modeled_assignment_state(state, **KEY)
    assert restore_modeled_assignment_state(rows, **KEY) == state
    from src.backend.live_assignment_state_snapshot import (
        STATE_TABLES, project_state_snapshot, recover_state_snapshot,
    )
    assert TABLE in STATE_TABLES
    flat, commit = project_state_snapshot(state, **KEY)

    class Storage:
        def read(self, table, identity):
            return deepcopy([commit] if table == STATE_TABLES[-1].name else flat[table])

    assert recover_state_snapshot(Storage(), **KEY,
                                  expected_commit_hash=commit["content_hash"]) == state


@pytest.mark.parametrize("value", [
    {"other": 1}, {"sample_at": 1}, {"sample_at": float("nan")},
    {"bullish": 1}, {"started_at": None}, {"session": "2026-09-24T00:00:00"},
])
def test_vwap_episode_rejects_unmodeled_source_values(value):
    with pytest.raises(ValueError):
        _project(value)


def test_vwap_episode_rejects_changed_content_and_orphan():
    row = _project({"bullish": True})
    changed = deepcopy(row)
    changed["bullish"] = False
    with pytest.raises(ValueError):
        restore_vwap_episode(changed)
    changed = deepcopy(row)
    changed["present"] = False
    with pytest.raises(ValueError):
        restore_vwap_episode(changed)
