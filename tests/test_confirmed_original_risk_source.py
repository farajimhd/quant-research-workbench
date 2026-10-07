from dataclasses import replace
from datetime import date

import polars as pl
import pytest

from src.backend.backtest_confirmed_original_risk_source import (
    CompiledCompletedRiskLookup, SOURCE_KEYS, compile_completed_risk_pairs,
)
from src.backend.backtest_market_data import CertifiedMarketDayPlan, MarketDayUnit, ExecutionInterval


def plan():
    units=tuple(MarketDayUnit('build','2026-01-01','TEST',stage,attempt,'a'*64,1,'b'*64)
        for stage,attempt in [('bars','00000000-0000-0000-0000-000000000001'),
                              ('technical','00000000-0000-0000-0000-000000000002'),
                              ('broker_100ms','00000000-0000-0000-0000-000000000003')])
    return CertifiedMarketDayPlan(ExecutionInterval('time',100),'build','c'*64,
        ('2026-01-01',),('TEST',),units,(100,5000),'d'*64)


def frame(clocks=(90000,95000,100000)):
    p=plan()
    return pl.DataFrame(dict(source_build_id=['build']*len(clocks),
        source_market_plan_token=[p.token]*len(clocks),
        source_bars_attempt_id=[p.units[0].attempt_id]*len(clocks),
        source_indicators_attempt_id=[p.units[1].attempt_id]*len(clocks),
        session_date=['2026-01-01']*len(clocks),ticker=['TEST']*len(clocks),
        source_liquidity_attempt_id=[p.units[2].attempt_id]*len(clocks),
        boundary_ms=list(clocks),close_int=[97500]*len(clocks),price_valid=[True]*len(clocks),
        macd_line=[.1]*len(clocks),macd_signal=[.2]*len(clocks)))


def lookup(f):return CompiledCompletedRiskLookup(f,plan=plan(),session_date=date(2026,1,1))


def test_exact_completed_pair_future_tail_independence():
    a=lookup(frame())
    b=lookup(frame((90000,95000,100000,105000)))
    assert a.pair_at('TEST',100000)==b.pair_at('TEST',100000)
    assert a.pair_at('TEST',90000) is None
    assert a.pair_at('TEST',99900) is None
    assert a.pair_at('OTHER',100000) is None


def test_gap_missing_momentum_and_price_do_not_skip_to_older_bucket():
    assert lookup(frame((90000,100000))).pair_at('TEST',100000) is None
    for key,value in [('price_valid',False),('macd_line',None),('close_int',None),('macd_line',.3)]:
        f=frame().with_columns(pl.when(pl.col('boundary_ms')==95000)
                              .then(pl.lit(value)).otherwise(pl.col(key)).alias(key))
        assert lookup(f).pair_at('TEST',100000) is None


@pytest.mark.parametrize('key',SOURCE_KEYS)
def test_foreign_source_identity_cannot_enter_active_lookup(key):
    bad={'session_date':'2026-01-02','ticker':'OTHER'}.get(key,'e'*64)
    with pytest.raises(ValueError,match='certified source plan'):
        lookup(frame().with_columns(pl.lit(bad).alias(key)))


def test_duplicates_and_overflow_fail_closed():
    with pytest.raises(ValueError,match='repeats'):lookup(frame((95000,95000)))
    with pytest.raises(ValueError,match='bounded'):
        CompiledCompletedRiskLookup(frame(),plan=plan(),session_date=date(2026,1,1),max_rows=2)


def test_columnar_shape_original_order_and_group_reset():
    f=frame((100000,90000,95000))
    c=compile_completed_risk_pairs(f)
    assert c['boundary_ms'].to_list()==[100000,90000,95000]
    f=f.with_columns(pl.when(pl.col('boundary_ms')==95000).then(pl.lit('other'))
                    .otherwise(pl.col('source_build_id')).alias('source_build_id'))
    assert not compile_completed_risk_pairs(f).filter(pl.col('boundary_ms')==100000)['pair_complete'].item()


def test_exact_plan_session_and_attempt_required():
    with pytest.raises(ValueError,match='session authority'):
        CompiledCompletedRiskLookup(frame(),plan=plan(),session_date=date(2026,1,2))
    with pytest.raises(ValueError,match='producer attempts'):
        CompiledCompletedRiskLookup(frame(),plan=replace(plan(),units=plan().units[:1]),session_date=date(2026,1,1))
