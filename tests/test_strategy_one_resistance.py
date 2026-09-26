"""Completed-bar Strategy 1 resistance acceptance never invents event order."""
import pytest

from src.trading_runtime.strategy_one_resistance import (
    ResistanceObservation, observe_completed_resistance_second,
)


def level(identity="r1", lower=10., upper=10.2):
    return {"unified_level_id": identity, "lower": lower, "upper": upper,
            "role": "resistance"}


def bar(boundary, opened, closed):
    return {"boundary_ms": boundary, "resolution_ms": 1_000,
            "open_int": opened, "close_int": closed, "price_valid": 1}


def test_prior_known_level_breaks_only_on_contiguous_green_completed_second():
    state, breaks = observe_completed_resistance_second(
        ResistanceObservation(), bar(1_000, 100_000, 100_500),
        admitted_levels=(level(),))
    assert breaks == ()  # The level did not exist in prior causal state.
    state, breaks = observe_completed_resistance_second(
        state, bar(2_000, 100_500, 101_500), admitted_levels=(level(),))
    assert [item.level["unified_level_id"] for item in breaks] == ["r1"]
    assert breaks[0].completed_boundary_ms == 2_000
    state, breaks = observe_completed_resistance_second(
        state, bar(3_000, 101_200, 101_600), admitted_levels=(level(),))
    assert breaks == ()
    state, breaks = observe_completed_resistance_second(
        state, bar(4_000, 101_100, 100_900), admitted_levels=(level(),))
    assert breaks == () and not state.accepted_ids
    _, breaks = observe_completed_resistance_second(
        state, bar(5_000, 100_900, 101_500), admitted_levels=(level(),))
    assert len(breaks) == 1


def test_gap_or_changed_level_cannot_manufacture_a_crossing():
    state, _ = observe_completed_resistance_second(
        ResistanceObservation(), bar(1_000, 100_000, 100_500),
        admitted_levels=(level(),))
    state, breaks = observe_completed_resistance_second(
        state, bar(3_000, 100_500, 101_500), admitted_levels=(level(),))
    assert breaks == ()
    state, breaks = observe_completed_resistance_second(
        state, bar(4_000, 100_500, 101_500),
        admitted_levels=(level(upper=10.3),))
    assert breaks == ()
    _, breaks = observe_completed_resistance_second(
        state, bar(5_000, 100_500, 102_000),
        admitted_levels=(level(upper=10.3),))
    assert len(breaks) == 1


def test_prior_resistance_identity_survives_same_geometry_role_flip():
    state, _ = observe_completed_resistance_second(
        ResistanceObservation(), bar(1_000, 100_000, 100_500),
        admitted_levels=(level(),))
    flipped = {**level(), "role": "support"}
    _, breaks = observe_completed_resistance_second(
        state, bar(2_000, 100_500, 101_500),
        admitted_levels=(flipped,))
    assert [item.level["unified_level_id"] for item in breaks] == ["r1"]


def test_legacy_side_only_resistance_is_admitted_without_new_event_order():
    side_only = {**level(), "role": "", "side": -1}
    state, _ = observe_completed_resistance_second(
        ResistanceObservation(), bar(1_000, 100_000, 100_500),
        admitted_levels=(side_only,))
    _, breaks = observe_completed_resistance_second(
        state, bar(2_000, 100_500, 101_500),
        admitted_levels=(side_only,))
    assert len(breaks) == 1


def test_future_or_non_price_second_is_rejected():
    state = ResistanceObservation(boundary_ms=1_000)
    with pytest.raises(ValueError, match="completed 1s price bar"):
        observe_completed_resistance_second(
            state, {**bar(1_000, 100_000, 101_000)},
            admitted_levels=(level(),))
    with pytest.raises(ValueError, match="completed 1s price bar"):
        observe_completed_resistance_second(
            state, {**bar(2_000, 100_000, 101_000), "price_valid": 0},
            admitted_levels=(level(),))
