from dataclasses import replace

import pytest

from src.trading_runtime.squeeze_ladder_setup import freeze_ladder_stop
from src.trading_runtime.strategy_one_bos import ConfirmedPivot


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
