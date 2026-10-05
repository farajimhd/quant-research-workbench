from dataclasses import replace

import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
from src.backend.backtest_squeeze_ladder_setup import bind_ladder_setups
from src.backend.backtest_strategy_one_pivot_store import CertifiedPivotPlan, CertifiedPivotCoverage
from src.backend.backtest_strategy_one_v7_interval_store import CertifiedV7IntervalPlan, CertifiedV7IntervalUnit
from src.trading_runtime.strategy_one_pivot_product import PivotInterval, interval_content_hash
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval, clock_hash, interval_hash
from tests.test_backtest_squeeze_ladder_loader import fixture
from tests.test_backtest_strategy_one_loader import DAY, TICKER, ATTEMPT


def plans():
    market, scan, policy, table, Reader = fixture()
    observed, = load_ladder_observations(market, session_date=DAY, tickers=(TICKER,),
        through_boundary_ms=70000, certified_scan=scan, policy=policy, client=Reader(table))
    origin = int(market_day_boundary(DAY, 0).timestamp()) * 1000
    levels = (V7LevelInterval('R1', 0, 0, 66000, 10.1, 10.2, 'resistance', '', origin - 1000, True),
              V7LevelInterval('R1', 0, 66000, 57600001, 10.1, 10.4, 'resistance', '', origin - 1000, True))
    clocks = tuple(range(1000, 71000, 1000))
    unit = CertifiedV7IntervalUnit(TICKER, 'derived', ATTEMPT, 'a'*64, 'b'*64, 'c'*64,
                                  'd'*64, 'legacy-unfiltered', len(clocks), len(levels),
                                  clock_hash(clocks), interval_hash(levels))
    v7 = CertifiedV7IntervalPlan('build', DAY, (unit,), ((TICKER, clocks),), ((TICKER, levels),), 'v7-token')
    interval = PivotInterval('low', 98000, (origin + 62000)*1000, (origin + 63000)*1000, 63000, None)
    pivot = CertifiedPivotPlan('build', DAY,
        (CertifiedPivotCoverage(TICKER, 'derived', ATTEMPT, 1, interval_content_hash((interval,))),),
        ((TICKER, (interval,)),), 'pivot-token')
    return observed, market, v7, pivot


def test_real_arrow_gate_binds_native_v7_and_confirmed_pivot_without_future_geometry():
    observed, market, v7, pivots = plans()
    setup, = bind_ladder_setups(observed, market=market, v7=v7, pivots=pivots,
                              tick_int=100, stop_buffer_ticks=1)
    assert setup.reason == 'setup_qualified'
    assert setup.admission_boundary_ms == 64000
    assert setup.qualification_boundary_ms == 65000
    assert setup.resistance.upper == 10.2
    assert setup.stop.stop_int == 97900
    assert setup.v7_plan_token == v7.token
    assert setup.pivot_plan_token == pivots.token


def test_missing_pivot_is_explicit_rejection_and_changed_attempt_is_error():
    observed, market, v7, pivots = plans()
    expired = replace(pivots.intervals[0][1][0], valid_to_boundary_ms=64000)
    empty = replace(pivots, intervals=((TICKER, (expired,)),),
                    coverage=(replace(pivots.coverage[0], content_hash=interval_content_hash((expired,))),))
    setup, = bind_ladder_setups(observed, market=market, v7=v7, pivots=empty,
                              tick_int=100, stop_buffer_ticks=1)
    assert setup.reason == 'confirmed_swing_stop_unavailable'
    assert setup.stop is None
    changed = replace(pivots, coverage=(replace(pivots.coverage[0], bars_attempt_id='other'),))
    with pytest.raises(ValueError, match='same bar attempt'):
        bind_ladder_setups(observed, market=market, v7=v7, pivots=changed,
                           tick_int=100, stop_buffer_ticks=1)
