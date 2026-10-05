from dataclasses import replace

import pyarrow as pa

from src.backend.backtest_squeeze_ladder_loader import load_ladder_observations
from src.backend.backtest_squeeze_ladder_setup import bind_ladder_setups
from src.backend.backtest_squeeze_ladder_entry import propose_ladder_breakout
from src.trading_runtime.strategy_one_v7_intervals import V7LevelInterval, interval_hash
from tests.test_backtest_squeeze_ladder_loader import fixture
from tests.test_backtest_squeeze_ladder_setup import plans
from tests.test_backtest_strategy_one_loader import DAY, TICKER


def prepared(*, lose_vwap=False, targets=True):
    _, market, v7, pivots = plans()
    _, scan, policy, table, Reader = fixture()
    for name, value in [('close_int', 102200), ('bid_int', 102200), ('ask_int', 102300)]:
        values = table[name].to_pylist()
        values[650] = value
        if lose_vwap and name == 'close_int':
            values[650] = 99900
        if lose_vwap:
            values[651] = value
        table = table.set_column(table.schema.get_field_index(name), name, pa.array(values, type=pa.int64()))
    observed, = load_ladder_observations(market, session_date=DAY, tickers=(TICKER,),
        through_boundary_ms=70000, certified_scan=scan, policy=policy, client=Reader(table))
    if targets:
        original = v7.intervals[0][1]
        extra = tuple(V7LevelInterval(f'R{ordinal}', ordinal, 0, 57600001, float(9+ordinal),
                                     float(9+ordinal)+.2, 'resistance', '', original[0].confirmed_at_ms, True)
                      for ordinal in (2,3,4))
        intervals = original + extra
        v7 = replace(v7, intervals=((TICKER, intervals),),
                     coverage=(replace(v7.coverage[0], interval_count=len(intervals), interval_hash=interval_hash(intervals)),))
    setup = bind_ladder_setups(observed, market=market, v7=v7, pivots=pivots,
                               tick_int=100, stop_buffer_ticks=1)[0]
    return observed, setup, v7


def decide(observed, setup, v7, boundary=65100):
    return propose_ladder_breakout(observed, setup, v7=v7, boundary_ms=boundary,
                                  tick_int=100, break_buffer_ticks=1, target_count=3, allocation='equal')


def test_native_observations_propose_three_fixed_target_lots_only_after_frozen_break():
    observed, setup, v7 = prepared()
    result = decide(observed, setup, v7)
    assert result.reason == 'entry_proposed'
    assert result.previous_close_int == 100200
    assert result.close_int == 102200
    assert result.entry_limit_int == 102300
    assert result.target_level_ids == ('R2','R3','R4')
    assert [item.profit_target_price for item in result.protection.slices] == [10.99,11.99,12.99]
    assert all(item.stop.price == 9.79 for item in result.protection.slices)
    assert abs(sum(item.quantity_fraction for item in result.protection.slices) - 1) < 1e-12


def test_missing_target_ladder_and_later_geometry_change_reject():
    observed, setup, v7 = prepared(targets=False)
    assert decide(observed, setup, v7).reason == 'complete_structural_targets_unavailable'
    observed, setup, v7 = prepared()
    assert decide(observed, setup, v7, boundary=66100).reason == 'frozen_resistance_invalidated'


def test_vwap_loss_invalidates_without_changing_the_stop_or_resistance():
    observed, setup, v7 = prepared(lose_vwap=True)
    assert decide(observed, setup, v7, boundary=65200).reason == 'qualified_vwap_lost'
    assert setup.stop.stop_int == 97900
    assert setup.resistance.upper == 10.2
