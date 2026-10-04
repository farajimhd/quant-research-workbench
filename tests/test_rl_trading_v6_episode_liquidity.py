import numpy as np
import polars as pl
import pytest
from research.rl_trading.v6.price_action_opportunities import Config, calculate
from research.rl_trading.v6.episode_liquidity import evidence


def activity(times):
    return pl.DataFrame(dict(time_us=np.asarray(times,dtype=np.int64)*1_000_000,
        volume=[200.]*len(times),trade_count=[2]*len(times)))


def bars(times, signs):
    prices=[10.+i*.1 for i in range(len(times))]
    return pl.DataFrame(dict(time_us=np.asarray(times,dtype=np.int64)*1_000_000,
        open=prices,high=prices,low=prices,close=prices,
        macd_line=signs,macd_signal=[0.]*len(times)))


def test_prior_window_excludes_target_and_has_exact_inclusive_threshold():
    a=activity(range(1,12))
    e=evidence(np.array([10,11])*1_000_000,a,Config())
    assert e['prior_trades_60s'].to_list()==[18,20]
    assert e['liquidity_eligible'].to_list()==[False,True]
    changed=a.with_columns(pl.when(pl.col('time_us')==11_000_000).then(999999.).otherwise(pl.col('volume')).alias('volume'))
    assert evidence(np.array([11])*1_000_000,changed,Config())['prior_shares_60s'][0]==2000


def test_sparse_nuv_style_gap_never_gets_normalized_entry():
    b=bars([145,15670,15671],[-1.,1.,1.])
    labels,_,pairs,trades=calculate(b,activity=activity([145,15670,15671]))
    assert trades.is_empty() and labels['entry_gain'].sum()==0
    assert set(labels['action'])=={'WAIT'}
    assert pairs['liquidity_accepted'].to_list()==[False]


def test_no_mid_episode_reopening_after_activity_improves():
    b=bars(range(1,21),[-1.]*12+[1.]*8)
    labels,_,pairs,_=calculate(b,activity=activity(range(1,21)))
    assert labels['liquidity_eligible'][15]
    assert not pairs['liquidity_accepted'][0]
    assert labels['entry_gain'].sum()==0


def test_gap_invalidates_otherwise_admitted_pair_and_preserves_candles():
    b=bars([11,12,13,30,31,32],[-1.,-1.,1.,1.,1.,1.])
    a=activity(list(range(1,14))+[30,31,32])
    labels,_,pairs,trades=calculate(b,activity=a)
    assert labels.height==b.height and trades.is_empty()
    assert pairs['liquidity_rejection_reason'][0]=='inactivity_gap'
    assert labels['exit_gain'].null_count()==labels.height


def test_liquid_pair_keeps_prior_price_math_and_one_exit_cluster():
    b=bars(range(11,25),[-1.]*3+[1.]*11)
    a=activity(range(1,25))
    actual,_,pairs,trades=calculate(b,activity=a)
    expected=calculate(b,Config(liquidity_gate=False))[0]
    assert pairs['liquidity_accepted'][0] and trades.height==1
    for column in ['entry_gain','entry_quality','exit_gain','exit_quality','reference_action']:
        assert actual[column].equals(expected[column])


def test_missing_activity_fails_closed():
    with pytest.raises(ValueError,match='Exact certified activity'):
        calculate(bars([1,2],[-1.,1.]))


def test_exact_adapter_binds_output_hash_and_all_activity_clocks(monkeypatch):
    from research.rl_trading.v6.episode_liquidity import read_activity
    from research.rl_trading.v1 import arte_source, arte_sql
    from research.rl_trading.v1.common import bounds
    from datetime import date
    day=date(2026,7,31)
    source=dict(build_id='build',units={str(day):{'NUV':{'bars':dict(attempt_id='attempt',output_rows=2,output_hash='123')}}})
    monkeypatch.setattr(arte_sql,'query',lambda *a:[dict(n=2,unique_keys=2,hash=123)])
    monkeypatch.setattr(arte_source,'frame',lambda *a:pl.DataFrame(dict(bucket_index=[14400,14410],volume=[100.,200.],trade_count=[1,2])))
    clocks=np.array([bounds(day)[0]+1_000_000,bounds(day)[0]+11_000_000])
    assert read_activity(None,source,day,'NUV',clocks)['trade_count'].to_list()==[1,2]
    with pytest.raises(ValueError,match='bank clocks'):
        read_activity(None,source,day,'NUV',clocks+1)
    monkeypatch.setattr(arte_sql,'query',lambda *a:[dict(n=2,unique_keys=2,hash=124)])
    with pytest.raises(ValueError,match='integrity'):
        read_activity(None,source,day,'NUV',clocks)
