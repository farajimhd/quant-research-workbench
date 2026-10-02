"""Prepared41 shares40's scalar/vectorized policy without admitting execution."""
import polars as pl
import pytest

from src.trading_runtime.strategy_half_risk_liquidity_fade import (
    HalfRiskLiquidityFadeFailure, numbered_liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_reason
from test_strategy_half_risk_liquidity_fade import supplied


def test_same_half_risk_witness_preserves_all_previous_release_behavior():
    value = supplied()
    witnesses = [numbered_liquidity_fade_failure(value, strategy_number=number)
                 for number in (39, 40, 41)]
    assert type(witnesses[0]) is HalfRiskLiquidityFadeFailure
    assert witnesses[0] == witnesses[1] == witnesses[2]
    for number in (35, 36, 37, 38):
        assert numbered_liquidity_fade_failure(value, strategy_number=number) is None
    assert liquidity_fade_reason(41) == 'strategy_forty_one_liquidity_fade_failure'
    with pytest.raises(ValueError):
        numbered_liquidity_fade_failure(value, strategy_number=42)


def test_compiled41_retains_same_complete_half_rate_window_without_market_reads():
    from src.backend.backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
    from test_liquidity_fade_manager_checkpoint_route import manager_case
    from test_strategy_liquidity_fade_market_source import native_case
    manager, witness, financial, _, _ = manager_case()
    _, observation, _, _, _, args = native_case()
    frame = pl.DataFrame([dict(source_build_id=observation['source_build_id'],
        session_date='2026-08-10', ticker=financial.ticker,
        source_attempt_id=observation['source_bars_attempt_id'],
        boundary_ms=candle.boundary_ms, trade_count=count)
        for candle, count in zip(witness.candles, (57, 18, 20, 10))])
    windows = []
    for number in (39, 40, 41):
        lookup = CompiledLiquidityFadeLookup(frame, plan=args['plan'],
            session_date=manager.runtime.config.anchor_date, strategy_number=number)
        windows.append(lookup.window_at(financial.ticker, witness.boundary_ms))
    assert windows[0] is not None and windows[0] == windows[1] == windows[2]
    older = CompiledLiquidityFadeLookup(frame, plan=args['plan'],
        session_date=manager.runtime.config.anchor_date, strategy_number=38)
    assert older.window_at(financial.ticker, witness.boundary_ms) is None
