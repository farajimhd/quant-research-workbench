from dataclasses import replace

import pytest

from src.trading_runtime.squeeze_ladder_setup import freeze_ladder_stop
from src.trading_runtime.squeeze_ladder_setup import freeze_ladder_resistance, ladder_resistance_retained
from src.trading_runtime.strategy_one_bos import ConfirmedPivot
from tests.test_strategy_one_v7_intervals import level


def test_resistance_freezes_nearest_band_above_vwap_and_never_retargets():
    levels = (level('far', lower=12., upper=12.2),
              level('near', lower=10.1, upper=10.2),
              level('below', lower=9.8, upper=9.9))
    frozen = freeze_ladder_resistance(active_levels=levels, qualification_boundary_ms=5000,
        qualification_epoch_ms=1_800_000_001_000, execution_vwap=10.)
    assert frozen.level_id == 'near'
    assert frozen.upper_comparison_int == 102000
    assert ladder_resistance_retained(frozen, levels)
    assert not ladder_resistance_retained(frozen, (levels[0],))
    assert not ladder_resistance_retained(frozen, (level('near', lower=10.1, upper=10.3),))
    assert not ladder_resistance_retained(frozen, (level('near', lower=10.1, upper=10.2, role='support'),))


def test_missing_resistance_and_future_confirmation_fail_closed():
    args = dict(qualification_boundary_ms=5000, qualification_epoch_ms=1_800_000_001_000,
                execution_vwap=10.)
    assert freeze_ladder_resistance(active_levels=(), **args) is None
    assert freeze_ladder_resistance(active_levels=(level(lower=9.8, upper=10.2),), **args) is None
    with pytest.raises(ValueError):
        freeze_ladder_resistance(active_levels=(level(confirmed=1_800_000_001_001),), **args)


def test_native_completed_v7_lookup_freezes_band_before_break_and_future_change():
    import numpy as np
    from src.trading_runtime.strategy_one_v7_intervals import V7IntervalProjector, levels_at
    from src.trading_runtime.squeeze_ladder_cross import completed_crossings
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0, levels=[level(lower=10.1, upper=10.2)], valid_completed_second=False)
    projector.observe_unchanged(boundary_ms=1000)
    projector.observe(boundary_ms=2000, levels=[level(lower=10.1, upper=10.4)], valid_completed_second=True)
    clocks, intervals = projector.finish()
    def visible(at):
        return levels_at(boundary_ms=at, seed_policy='legacy-unfiltered',
                         valid_seconds=clocks, intervals=intervals)
    frozen = freeze_ladder_resistance(active_levels=visible(1000), qualification_boundary_ms=1000,
        qualification_epoch_ms=1_800_000_001_000, execution_vwap=10.)
    assert frozen.upper == 10.2
    assert ladder_resistance_retained(frozen, visible(1200))
    witness = completed_crossings(boundary_ms=np.array([1100,1200], dtype=np.int64),
        close_int=np.array([102000,102200], dtype=np.int64), price_valid=np.ones(2,dtype=np.uint8),
        reference_int=np.full(2,frozen.upper_comparison_int,dtype=np.int64),
        reference_available_ms=np.full(2,1000,dtype=np.int64), reference_valid=np.ones(2,dtype=np.uint8),
        admitted_at_ms=frozen.qualification_boundary_ms, buffer_int=100, maximum_reference_age_ms=1000)
    assert witness.crossing.tolist() == [False,True]
    assert not ladder_resistance_retained(frozen, visible(2000))
    assert frozen.upper == 10.2


def test_latest_confirmed_low_is_frozen_with_downward_tick_buffer():
    earlier = ConfirmedPivot("old", "low", 97000, 1000, 2000)
    latest = ConfirmedPivot("latest", "low", 98057, 3000, 4000)
    high = ConfirmedPivot("high", "high", 103000, 4000, 5000)
    frozen = freeze_ladder_stop(active_pivots=(high, latest, earlier),
        qualification_boundary_ms=5000, entry_limit_int=100000, tick_int=100, buffer_ticks=1)
    assert frozen.pivot == latest
    assert frozen.stop_int == 97900
    assert frozen.buffer_int == 100
    assert latest.price_int == 98057


def test_missing_and_wrong_side_latest_low_reject_without_older_fallback():
    args = dict(qualification_boundary_ms=5000, entry_limit_int=100000, tick_int=100)
    assert freeze_ladder_stop(active_pivots=(), **args) is None
    older = ConfirmedPivot("older", "low", 97000, 1000, 2000)
    wrong = ConfirmedPivot("newer", "low", 101000, 3000, 4000)
    assert freeze_ladder_stop(active_pivots=(older, wrong), **args) is None


@pytest.mark.parametrize("changed", [dict(confirmed_boundary_ms=5100),
                                     dict(price_int=0), dict(pivot_boundary_ms=4000)])
def test_future_or_malformed_source_is_contract_error(changed):
    pivot = replace(ConfirmedPivot("low", "low", 98000, 3000, 4000), **changed)
    with pytest.raises(ValueError):
        freeze_ladder_stop(active_pivots=(pivot,), qualification_boundary_ms=5000,
                           entry_limit_int=100000, tick_int=100)
