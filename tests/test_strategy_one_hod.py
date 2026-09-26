"""Late-HOD entry context uses only completed 100ms price buckets."""

import pytest

from src.trading_runtime.strategy_one_hod import (
    HodObservation, late_hod_admitted, observe_completed_hod,
)


LEVEL = {"unified_level_id": "r11", "lower": 10.9, "upper": 11.1,
         "role": "resistance", "side": "resistance"}


def bar(boundary: int, opened: int, closed: int, *, high: int = 120_000):
    return {"boundary_ms": boundary, "resolution_ms": 100,
            "price_valid": 1, "extremes_valid": 1,
            "open_int": opened, "high_int": high,
            "low_int": min(opened, closed), "close_int": closed}


def test_completed_crossing_below_prior_hod_opens_late_gate():
    state = observe_completed_hod(
        HodObservation(), bar(100, 100_000, 109_000),
        admitted_levels=(LEVEL,))
    assert state.late_mode and state.prior_hod_int == 100_000
    assert not late_hod_admitted(state, price_int=109_000, boundary_ms=100)
    state = observe_completed_hod(
        state, bar(200, 109_000, 109_000, high=109_000),
        admitted_levels=(LEVEL,))
    state = observe_completed_hod(
        state, bar(300, 109_000, 111_000, high=111_000),
        admitted_levels=(LEVEL,))
    assert state.prior_hod_int == 120_000
    assert state.gate is not None
    assert late_hod_admitted(state, price_int=111_000, boundary_ms=300)


def test_missing_price_bucket_cannot_create_crossing():
    state = observe_completed_hod(
        HodObservation(), bar(100, 100_000, 109_000),
        admitted_levels=(LEVEL,))
    state = observe_completed_hod(
        state, bar(200, 109_000, 109_000, high=109_000),
        admitted_levels=(LEVEL,))
    state = observe_completed_hod(state, {
        "boundary_ms": 300, "resolution_ms": 100, "price_valid": 0,
    }, admitted_levels=(LEVEL,))
    state = observe_completed_hod(
        state, bar(400, 109_000, 111_000, high=111_000),
        admitted_levels=(LEVEL,))
    assert state.gate is None


def test_changed_level_cannot_authorize_prior_crossing():
    state = observe_completed_hod(
        HodObservation(), bar(100, 100_000, 109_000),
        admitted_levels=(LEVEL,))
    state = observe_completed_hod(
        state, bar(200, 109_000, 109_000, high=109_000),
        admitted_levels=(LEVEL,))
    changed = {**LEVEL, "lower": 10.8}
    state = observe_completed_hod(
        state, bar(300, 109_000, 111_000, high=111_000),
        admitted_levels=(changed,))
    assert state.gate is None
    with pytest.raises(ValueError, match="clock"):
        late_hod_admitted(state, price_int=111_000, boundary_ms=200)
