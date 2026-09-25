from copy import deepcopy

import pytest

from src.trading_runtime import pullback_impulse as I
from src.trading_runtime.arte_assignment_vwap_pullback_move import (
    TABLE, project_entry_pullback_move, restore_entry_pullback_move,
    validate_entry_pullback_move,
)
from src.trading_runtime.arte_assignment_state_composite import project_modeled_assignment_state
from tests.test_arte_assignment_state_composite import KEY, _state


IDENTITY = {key: KEY[key] for key in ("assignment_id", "revision", "snapshot_id", "session")}


def _move():
    return dict(id=30.0, base=100, base_at=0.0, peak=105.0,
                peak_at=30.0, corrected=True, retracement=0.3)


def test_pullback_move_named_scalar_cold_readback_preserves_numeric_kind():
    move = _move()
    row = project_entry_pullback_move(move, **IDENTITY)
    class FakeStorage:
        def read(self):
            return deepcopy(row)
    assert restore_entry_pullback_move(FakeStorage().read()) == move
    assert row["base_int"] == 100 and row["base_float"] is None
    assert row["peak_float"] == 105.0 and row["peak_int"] is None
    assert restore_entry_pullback_move(project_entry_pullback_move(None, **IDENTITY)) is None
    assert all("JSON" not in kind and "Map" not in kind for _, kind in TABLE.columns)


def test_real_qualify_typed_mode_preserves_legacy_and_rejects_unmodeled_move():
    market = {"pullback_impulse": {"move": dict(id=30.0, base=100,
                                                base_at=0.0, peak=105.0,
                                                peak_at=30.0)}}
    anchor = {"swing": {"pivot_at": 32.0, "price": 103.5}}
    expected = I.qualify(market, anchor, [])
    assert I.qualify(market, anchor, [], typed_persistence=True) == expected
    market["pullback_impulse"]["move"]["untyped"] = {"value": 1}
    assert "untyped" in I.qualify(market, anchor, [])
    with pytest.raises(ValueError):
        I.qualify(market, anchor, [], typed_persistence=True)


@pytest.mark.parametrize("change", [
    {"unknown": 1}, {"base": True}, {"peak": float("nan")},
    {"id": 30}, {"corrected": 1}, {"invalid": None},
    {"retracement": 0.6},
])
def test_pullback_move_rejects_unmodeled_source(change):
    with pytest.raises(ValueError):
        validate_entry_pullback_move({**_move(), **change})


def test_pullback_move_corruption_and_full_entry_still_fail_closed():
    row = project_entry_pullback_move(_move(), **IDENTITY)
    changed = deepcopy(row)
    changed["peak_float"] = 106.0
    with pytest.raises(ValueError):
        restore_entry_pullback_move(changed)
    changed = deepcopy(row)
    changed["snapshot_id"] = "02e19bda-3786-4ee2-babe-05ad05bfc10a"
    with pytest.raises(ValueError):
        restore_entry_pullback_move(changed)
    with pytest.raises(ValueError, match="unmodeled"):
        project_modeled_assignment_state(
            {**_state(), "vwap_ladder_entry": {"pullback_move": _move()}}, **KEY)
