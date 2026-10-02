"""Pure prepared-rule checks; no numbered runtime or profitability acceptance."""
from dataclasses import replace
from decimal import Decimal
import random

import pytest

from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_liquidity_fade_failure import (
    MAX_TRADE_COUNT, LiquidityFadeCandle, LiquidityFadeInput, liquidity_fade_failure,
)
from src.trading_runtime.strategy_liquidity_fade_exit import validate_liquidity_fade_witness
from src.trading_runtime.strategy_half_risk_liquidity_fade import (
    HalfRiskLiquidityFadeFailure, half_risk_liquidity_fade_failure,
)


def supplied(*, opening=0, counts=(100, 100, 60, 40), boundary=40100,
             close=38000, bid=3.8, quote_age=100000):
    completed=opening+40000
    x=FollowThroughFailureInput(opening+boundary,opening+20000,4.0,3.6,
        completed,close,True,0.01,0.02,bid,4.0,quote_age,100.0,False)
    candles=tuple(LiquidityFadeCandle(completed-offset,count)
                  for offset,count in zip((15000,10000,5000,0),counts))
    return LiquidityFadeInput(x,candles)


@pytest.mark.parametrize('opening',[0,43200000])
def test_half_risk_confirmed_at_next_100ms_and_distinct_from_parent(opening):
    value=supplied(opening=opening)
    witness=half_risk_liquidity_fade_failure(value)
    assert type(witness) is HalfRiskLiquidityFadeFailure
    assert witness.candles==value.candles
    assert witness.boundary_ms==opening+40100
    assert liquidity_fade_failure(value) is None  # Activity is half, not quarter.
    with pytest.raises(ValueError,match='complete typed witness'):
        validate_liquidity_fade_witness(witness)


@pytest.mark.parametrize('close,bid,expected',[
    (38000,3.8,True),(38001,3.8,False),(38000,3.8001,False),
    (37999,3.7999,True),(39000,3.9,False),
])
def test_both_completed_close_and_executable_bid_must_cross_half_risk(close,bid,expected):
    assert (half_risk_liquidity_fade_failure(supplied(close=close,bid=bid)) is not None)==expected


def test_preserve_yj_like_shallow_pullback_despite_activity_decline():
    value=supplied(counts=(700,751,300,396),close=36697,bid=3.66)
    value=replace(value,five_second=replace(value.five_second,reference_ask=3.73,initial_stop=3.25))
    assert half_risk_liquidity_fade_failure(value) is None


@pytest.mark.parametrize('change',[
    dict(position_quantity=0.0),dict(pending_exit=True),dict(price_valid=False),
    dict(macd_line=0.02),dict(macd_line=float('nan')),dict(macd_signal=None),
    dict(quote_age_us=None),dict(quote_age_us=1000001),dict(quote_age_us=-1),
    dict(bid=0.0),dict(ask=3.7),dict(completed_five_second_close_int=None),
    dict(completed_five_second_close_int=True),dict(completed_five_second_boundary_ms=45000),
    dict(completed_five_second_boundary_ms=35000),dict(first_held_boundary_ms=20100),
])
def test_incomplete_stale_forming_or_nonheld_inputs_never_signal(change):
    value=supplied()
    value=replace(value,five_second=replace(value.five_second,**change))
    assert half_risk_liquidity_fade_failure(value) is None


@pytest.mark.parametrize('boundary',[45000,25000000,43200000])
def test_stale_bar_or_regular_hours_cannot_confirm(boundary):
    value=supplied(boundary=boundary)
    assert half_risk_liquidity_fade_failure(value) is None


def test_elapsed_time_alone_never_exits_and_long_hold_can_confirm():
    value=supplied(boundary=3600100)
    completed=3600000
    value=replace(value,five_second=replace(value.five_second,completed_five_second_boundary_ms=completed),
        candles=tuple(LiquidityFadeCandle(completed-offset,count)
                      for offset,count in zip((15000,10000,5000,0),(100,100,60,40))))
    assert half_risk_liquidity_fade_failure(value) is not None
    assert half_risk_liquidity_fade_failure(replace(value,
        five_second=replace(value.five_second,completed_five_second_close_int=40000,bid=4.0))) is None


def test_gaps_and_missing_count_observations_never_become_zero():
    value=supplied()
    assert half_risk_liquidity_fade_failure(replace(value,candles=value.candles[1:])) is None
    bad=replace(value.candles[1],boundary_ms=value.candles[1].boundary_ms+5000)
    assert half_risk_liquidity_fade_failure(replace(value,candles=(value.candles[0],bad,*value.candles[2:]))) is None
    for count in (None,True,-1,MAX_TRADE_COUNT+1):
        bad=replace(value.candles[0],trade_count=count)
        with pytest.raises(ValueError):half_risk_liquidity_fade_failure(replace(value,candles=(bad,*value.candles[1:])))


def test_exact_uint64_count_ratio_against_integer_oracle():
    rng=random.Random(39)
    cases=[(MAX_TRADE_COUNT,MAX_TRADE_COUNT,MAX_TRADE_COUNT,0),
           (MAX_TRADE_COUNT,MAX_TRADE_COUNT,MAX_TRADE_COUNT,1),
           (100,101,50,50),(100,101,50,51),(0,0,0,0)]
    cases.extend(tuple(rng.randrange(1<<64) for _ in range(4)) for _ in range(128))
    for counts in cases:
        expected=sum(counts[:2])>0 and 2*sum(counts[2:])<=sum(counts[:2])
        value=supplied(counts=counts)
        assert (half_risk_liquidity_fade_failure(value) is not None)==expected


def test_decimal_original_price_equality_is_not_float_rounding():
    value=supplied(close=22200,bid=2.22)
    value=replace(value,five_second=replace(value.five_second,reference_ask=2.26,initial_stop=2.18,ask=2.23))
    assert (Decimal('2.26')+Decimal('2.18'))/2==Decimal('2.22')
    assert half_risk_liquidity_fade_failure(value) is not None
