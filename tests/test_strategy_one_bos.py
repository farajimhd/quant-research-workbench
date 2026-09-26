"""Strategy 1 BOS uses certified completed bars and prior-known pivots."""
import pytest

from src.trading_runtime.strategy_one_bos import (
    BosObservation, ConfirmedPivot, observe_completed_bos,
)


def bar(boundary, close):
    return {"boundary_ms": boundary, "resolution_ms": 1_000,
            "close_int": close, "price_valid": 1}


def pivot(confirmed=2_000):
    return ConfirmedPivot("high-1", "high", 101_000, 1_000, confirmed)


def test_new_pivot_cannot_retroactively_authorize_same_bar_break():
    state, event = observe_completed_bos(
        BosObservation(), bar(2_000, 100_000), confirmed_pivots=(pivot(),))
    assert event is None and state.reference == pivot()
    state, event = observe_completed_bos(
        state, bar(3_000, 102_000), confirmed_pivots=(pivot(),))
    assert event is not None and event.broken_pivot == pivot()
    assert event.close_int == 102_000


def test_missing_second_cannot_supply_a_fictional_crossing():
    state, _ = observe_completed_bos(
        BosObservation(), bar(2_000, 100_000), confirmed_pivots=(pivot(),))
    state, event = observe_completed_bos(
        state, bar(4_000, 102_000), confirmed_pivots=(pivot(),))
    assert event is None
    _, event = observe_completed_bos(
        state, bar(5_000, 103_000), confirmed_pivots=(pivot(),))
    assert event is None


def test_newer_pivot_replaces_reference_only_after_current_crossing():
    state, _ = observe_completed_bos(
        BosObservation(), bar(2_000, 100_000), confirmed_pivots=(pivot(),))
    newer = ConfirmedPivot("high-2", "high", 105_000, 2_000, 3_000)
    state, event = observe_completed_bos(
        state, bar(3_000, 102_000), confirmed_pivots=(pivot(), newer))
    assert event is not None and event.broken_pivot == pivot()
    assert state.reference == newer


def test_future_or_malformed_pivot_fails_closed():
    with pytest.raises(ValueError, match="future"):
        observe_completed_bos(
            BosObservation(), bar(2_000, 100_000),
            confirmed_pivots=(pivot(confirmed=3_000),))
    with pytest.raises(ValueError, match="completed 1s"):
        observe_completed_bos(
            BosObservation(), {**bar(2_000, 100_000), "price_valid": 0},
            confirmed_pivots=())
