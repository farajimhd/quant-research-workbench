"""Columnar necessary-condition checks; no source or runtime admission."""
import random

import polars as pl
import pytest

from src.backend.backtest_strategy_liquidity_fade import compile_liquidity_fade_observations
from src.backend.backtest_strategy_half_risk_liquidity_fade import (
    HALF_RISK_ACTIVITY_FADE, compile_half_risk_liquidity_observations,
)


def frame(counts, *, clocks=None, tickers=None):
    n=len(counts)
    return pl.DataFrame(dict(source_build_id=['b']*n,session_date=['2026-08-18']*n,
        ticker=tickers or ['X']*n,source_attempt_id=['a']*n,
        boundary_ms=clocks or [5000*(i+1) for i in range(n)],
        trade_count=pl.Series(counts,dtype=pl.UInt64)))


def test_preserve_parent_shape_order_and_quarter_flag():
    original=frame([100,100,60,40]);before=original.clone()
    parent=compile_liquidity_fade_observations(original)
    result=compile_half_risk_liquidity_observations(original)
    assert original.equals(before)
    assert result.drop(HALF_RISK_ACTIVITY_FADE).equals(parent)
    assert result[HALF_RISK_ACTIVITY_FADE].to_list()==[False,False,False,True]
    assert parent['liquidity_fade'].to_list()==[False]*4


@pytest.mark.parametrize('counts,clocks,tickers',[
    ([100,None,60,40],None,None),([100,100,60,40],[5000,10000,20000,25000],None),
    ([100,100,60,40],[5000,10000,5000,10000],['X','X','Y','Y']),
    ([0,0,0,0],None,None),
])
def test_missing_gapped_cross_ticker_and_zero_prior_do_not_signal(counts,clocks,tickers):
    result=compile_half_risk_liquidity_observations(frame(counts,clocks=clocks,tickers=tickers))
    assert not result[HALF_RISK_ACTIVITY_FADE].any()


def test_full_uint64_windows_match_integer_oracle_without_overflow():
    maximum=(1<<64)-1;rng=random.Random(39)
    cases=[(maximum,maximum,maximum,0),(maximum,maximum,maximum,1),(100,101,50,51)]
    cases.extend(tuple(rng.randrange(1<<64) for _ in range(4)) for _ in range(64))
    counts=[v for values in cases for v in values]
    tickers=[str(i) for i in range(len(cases)) for _ in range(4)]
    result=compile_half_risk_liquidity_observations(frame(counts,clocks=[5000,10000,15000,20000]*len(cases),tickers=tickers))
    assert result[HALF_RISK_ACTIVITY_FADE].to_list()[3::4]==[
        sum(x[:2])>0 and 2*sum(x[2:])<=sum(x[:2]) for x in cases]


def test_existing_flag_cannot_be_overwritten():
    with pytest.raises(ValueError,match='overwrite'):
        compile_half_risk_liquidity_observations(frame([1,1,1,1]).with_columns(pl.lit(True).alias(HALF_RISK_ACTIVITY_FADE)))
