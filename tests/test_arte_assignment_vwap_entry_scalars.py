from copy import deepcopy

import pytest

from src.trading_runtime.arte_assignment_vwap_entry_scalars import (
    TABLE, INITIAL, OPTIONAL, project_entry_scalars, restore_entry_scalars,
    validate_typed_entry_scalars,
)
from src.trading_runtime import vwap_resistance_ladder as V
from src.trading_runtime.arte_assignment_state_composite import project_modeled_assignment_state
from tests.test_arte_assignment_state_composite import KEY, _state
from tests.test_vwap_resistance_ladder import fixture


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def _scalars():
    return dict(entry_price=10, requested_at=1790258400.0, late=False,
                target_moves=0, add_opportunities=0, episode_id=None,
                entry_kind="initial", prior_episode_used=False)


def test_initial_and_mutable_scalars_exact_cold_readback():
    source = {**_scalars(), "cross_at": 1790258401.0, "cross_price": 10.5,
              "slice_notional": 3000.0, "first_fill_at": 1790258401.5,
              "pending_target": {"price": 12, "moves": 1}}
    row = project_entry_scalars(source, **IDENTITY)
    class FakeStorage:
        def read(self):
            return deepcopy(row)
    assert restore_entry_scalars(FakeStorage().read()) == source
    assert row["entry_price_int"] == 10 and row["entry_price_float"] is None
    assert row["pending_target_price_int"] == 12
    assert restore_entry_scalars(project_entry_scalars(None, **IDENTITY)) is None
    assert all("JSON" not in kind and "Map" not in kind for _, kind in TABLE.columns)


def test_actual_opt_in_entry_producer_emits_closed_scalar_subset():
    host, assignment, observation = fixture()
    result = V.evaluate(host, assignment, observation(), assignment.parameters,
                        assignment.state, typed_persistence=True)
    entry = result.state["vwap_ladder_entry"]
    scalars = validate_typed_entry_scalars({key: entry[key] for key in INITIAL | OPTIONAL
                                            if key in entry})
    assert scalars["entry_kind"] == "initial"
    assert restore_entry_scalars(project_entry_scalars(scalars, **IDENTITY)) == scalars


@pytest.mark.parametrize("change", [
    {"unknown": 1}, {"entry_price": True}, {"requested_at": 1},
    {"late": 1}, {"target_moves": -1}, {"episode_id": "clock"},
    {"entry_kind": "future"}, {"slice_notional": None},
    {"pending_target": {"price": 12}},
    {"pending_target": {"price": 12.0, "moves": True}},
])
def test_scalar_source_rejects_unmodeled_value(change):
    with pytest.raises(ValueError):
        validate_typed_entry_scalars({**_scalars(), **change})


def test_scalar_cold_read_rejects_missing_pending_and_mixed_identity():
    row = project_entry_scalars({**_scalars(),
                                 "pending_target": {"price": 12.0, "moves": 1}},
                                **IDENTITY)
    changed = deepcopy(row)
    changed["pending_target_price_float"] = None
    with pytest.raises(ValueError):
        restore_entry_scalars(changed)
    changed = deepcopy(row)
    changed["snapshot_id"] = "02e19bda-3786-4ee2-babe-05ad05bfc10a"
    with pytest.raises(ValueError):
        restore_entry_scalars(changed)
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state(
            {**_state(), "vwap_ladder_entry": _scalars()}, **KEY)
