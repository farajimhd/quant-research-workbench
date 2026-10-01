"""Completed-only constant-size lookup; future, stale, missing and foreign reject."""
from dataclasses import replace

import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
from test_strategy_liquidity_fade_market_source import native_case


def fixture():
    witness, _, bars, _, _, args = native_case()
    frame = pl.DataFrame(bars).select(
        pl.col('build_id').alias('source_build_id'), 'session_date', 'ticker',
        pl.col('attempt_id').alias('source_attempt_id'),
        ((pl.col('bucket_index')+1)*5000-14_400_000).alias('boundary_ms'),
        pl.col('trade_count').cast(pl.UInt64),
    )
    return witness, frame, dict(plan=args['plan'], session_date=args['session_date'])


def test_latest_completed_lookup_can_wait_for_fresh_quote_but_cannot_use_future():
    witness, frame, args = fixture()
    cache = CompiledLiquidityFadeLookup(frame.reverse(), **args)
    assert cache.window_at('PLUG', witness.completed_five_second_boundary_ms-100) is None
    result = cache.window_at('PLUG', witness.boundary_ms)
    assert result.boundary_ms == witness.completed_five_second_boundary_ms
    assert result.candles == witness.candles
    assert cache.window_at('PLUG', witness.completed_five_second_boundary_ms+4900) == result
    assert cache.window_at('PLUG', witness.completed_five_second_boundary_ms+5000) is None
    assert frame.height == 4 and frame['trade_count'].to_list() == [57, 18, 8, 5]


def test_next_completed_nonfade_row_invalidates_old_signal_without_filtering_it():
    witness, frame, args = fixture()
    next_row = frame.tail(1).with_columns(
        (pl.col('boundary_ms')+5000).alias('boundary_ms'), pl.lit(100, dtype=pl.UInt64).alias('trade_count'))
    cache = CompiledLiquidityFadeLookup(pl.concat([frame, next_row]), **args)
    assert cache.window_at('PLUG', witness.boundary_ms) is not None
    assert cache.window_at('PLUG', witness.completed_five_second_boundary_ms+5000) is None


def test_missing_observations_do_not_turn_into_zero_activity():
    witness, frame, args = fixture()
    missing = frame.with_columns(pl.Series('trade_count', [57, None, 8, 5], dtype=pl.UInt64))
    assert CompiledLiquidityFadeLookup(missing, **args).window_at('PLUG', witness.boundary_ms) is None
    assert CompiledLiquidityFadeLookup(frame.head(0), **args).window_at('PLUG', witness.boundary_ms) is None
    zero = frame.with_columns(pl.Series('trade_count', [57, 18, 0, 0], dtype=pl.UInt64))
    assert CompiledLiquidityFadeLookup(zero, **args).window_at('PLUG', witness.boundary_ms) is not None


def test_uint64_counts_are_not_converted_to_lossy_nullable_float_arrays():
    witness, frame, args = fixture()
    maximum = (1 << 64)-1
    large = frame.with_columns(pl.Series('trade_count', [maximum, maximum, 1, 0], dtype=pl.UInt64))
    window = CompiledLiquidityFadeLookup(large, **args).window_at('PLUG', witness.boundary_ms)
    assert tuple(c.trade_count for c in window.candles) == (maximum, maximum, 1, 0)


@pytest.mark.parametrize('column,value', [('source_build_id', 'f'*64),
    ('session_date', '2026-08-11'), ('ticker', 'OTHER'), ('source_attempt_id', 'foreign')])
def test_foreign_native_frame_scope_rejects(column, value):
    _, frame, args = fixture()
    with pytest.raises(ValueError, match='pinned source'):
        CompiledLiquidityFadeLookup(frame.with_columns(pl.lit(value).alias(column)), **args)


def test_plan_population_and_decision_scope_cannot_be_silently_truncated():
    _, frame, args = fixture()
    for plan in (replace(args['plan'], units=args['plan'].units[1:]),
                 replace(args['plan'], units=args['plan'].units + args['plan'].units[:1])):
        with pytest.raises(ValueError):
            CompiledLiquidityFadeLookup(frame, **dict(args, plan=plan))
    cache = CompiledLiquidityFadeLookup(frame, **args)
    for ticker, boundary in (('OTHER', 44_807_400), ('PLUG', True), ('PLUG', 44_807_401)):
        with pytest.raises(ValueError):
            cache.window_at(ticker, boundary)
    with pytest.raises(ValueError, match='row/memory bound'):
        CompiledLiquidityFadeLookup(frame, **args, max_rows=3)
