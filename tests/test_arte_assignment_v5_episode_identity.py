from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import v5_macd_episode as M
from src.trading_runtime.arte_assignment_v5_episode_identity import (
    TABLE, project_v5_episode_identity, restore_v5_episode_identity,
)
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_episode_reentry_stop import setup


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def test_acquired_and_held_episode_clocks_exact_cold_roundtrip():
    source = {"last_acquired_macd_episode": 1790258400.0,
              "last_held_macd_episode": 1790258460.0}
    row = project_v5_episode_identity(source, **IDENTITY)
    assert restore_v5_episode_identity(deepcopy(row)) == source
    assert restore_v5_episode_identity(None) == {}
    assert project_v5_episode_identity({}, **IDENTITY) is None
    assert all("JSON" not in kind and "Map" not in kind for _, kind in TABLE.columns)


def test_real_v5_observer_held_episode_is_closed_source():
    parameters, state, observation = setup()
    below = replace(observation,
                    observed_at=observation.observed_at + timedelta(seconds=1),
                    macd_line=.51, position_quantity=10., price=103.5,
                    bar_open=103.5, source_timeframe="1s",
                    evaluation_events=("bar_close",))
    M.observe(below, parameters, state)
    resumed = replace(below, observed_at=below.observed_at + timedelta(seconds=1),
                      macd_line=1., price=103.6, bar_open=103.55)
    M.observe(resumed, parameters, state)
    clock = state["last_held_macd_episode"]
    assert type(clock) is float
    assert restore_v5_episode_identity(project_v5_episode_identity(
        {"last_held_macd_episode": clock}, **IDENTITY)) == {
            "last_held_macd_episode": clock}


def test_v5_clocks_join_exact_assignment_state_cold_snapshot():
    source = {**_state(), "last_acquired_macd_episode": 1790258400.0,
              "last_held_macd_episode": 1790258460.0}
    projected = project_modeled_assignment_state(source, **KEY)
    assert restore_modeled_assignment_state(projected, **KEY) == source
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


@pytest.mark.parametrize("value", [
    {"last_held_macd_episode": 0.0},
    {"last_held_macd_episode": 1},
    {"last_acquired_macd_episode": float("nan")},
    {"last_acquired_macd_episode": None},
    {"unknown": 1.0},
])
def test_v5_episode_rejects_unmodeled_or_noncausal_value(value):
    with pytest.raises(ValueError):
        project_v5_episode_identity(value, **IDENTITY)


def test_v5_episode_rejects_hash_and_identity_corruption():
    row = project_v5_episode_identity(
        {"last_held_macd_episode": 1790258400.0}, **IDENTITY)
    changed = deepcopy(row)
    changed["held_at"] += 1
    with pytest.raises(ValueError):
        restore_v5_episode_identity(changed)
    changed = deepcopy(row)
    changed["assignment_id"] = "other"
    with pytest.raises(ValueError):
        restore_v5_episode_identity(changed)
