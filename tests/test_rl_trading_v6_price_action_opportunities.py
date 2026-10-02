"""Episode-local opportunity quality, conditional alternatives and sparse refs."""
import numpy as np
import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from research.rl_trading.v6 import price_action_opportunities as op
from src.backend.research_model_service import router


def bars(prices,signs,times=None):
    n=len(prices)
    return pl.DataFrame(dict(time_us=[int(t*1e6) for t in (times or range(1,n+1))],
        open=prices,close=prices,high=[p+.1 for p in prices],low=[p-.1 for p in prices],
        macd_line=signs,macd_signal=[0.]*n))


def test_several_opportunities_around_swing_and_one_reference_pair():
    prices=[10.,9.,9.05,9.1,10.,10.9,11.,10.95]
    labels,episodes,pairs,trades=op.calculate(bars(prices,[-1.,-1.,-1.,1.,1.,1.,1.,1.]))
    pair=pairs.row(0,named=True)
    assert trades.height==1
    assert labels.filter(pl.col('action')=='ENTRY').height>=2
    assert labels.filter(pl.col('action')=='EXIT').height>=2
    assert labels.filter(pl.col('reference_action')=='ENTRY').height==1
    assert labels.filter(pl.col('reference_action')=='EXIT').height==1
    assert pair['reference_exit_us']>pair['reference_entry_us']
    assert pair['reference_exit_price']==11.
    for i,r in enumerate(labels.iter_rows(named=True)):
        future=[j for j in range(i+1,len(prices)) if j>=3]
        expected=max([0.]+[(prices[j]-prices[i])*2**(-(j-i)/30.) for j in future])
        assert r['entry_gain']==pytest.approx(expected)
        assert r['entry_quality']==pytest.approx(expected/pair['best_entry_gain'])
        if r['exit_quality'] is not None:
            gain=prices[i]-pair['reference_entry_price']
            assert r['exit_quality']==pytest.approx(np.clip(gain/pair['best_exit_gain'],0,1))
    assert labels['entry_quality'].max()==1.
    assert labels['exit_quality'].max()==1.


def test_later_pair_does_not_inflate_local_scores_and_carry_is_not_added():
    first=bars([10.,9.,10.,11.],[-1.,-1.,1.,1.])
    labels,_,_,_=op.calculate(first)
    extended=bars([10.,9.,10.,11.,1.,10.,20.],[-1.,-1.,1.,1.,-1.,1.,1.])
    combined,_,pairs,trades=op.calculate(extended)
    assert combined.head(4)['entry_quality'].to_list()==labels['entry_quality'].to_list()
    assert combined.head(4)['entry_gain'].to_list()==labels['entry_gain'].to_list()
    assert combined.head(4)['exit_quality'].to_list()==labels['exit_quality'].to_list()
    assert combined.head(4)['carried_next_pair_value'].max()>0
    assert combined['label_value'].max()<=1
    assert trades['pair_id'].n_unique()==trades.height==2
    assert pairs['carried_next_pair_value'][0]>0
    # Discount uses absolute elapsed clocks for comparison at pair start.
    assert pairs['carried_next_pair_value'][0]==pytest.approx(pairs['best_available_opportunity_value'][1]*2**(-4/30))


def test_elapsed_gap_discount_and_chronological_exit_only_in_long():
    labels,_,pairs,trades=op.calculate(bars([9.,10.,11.],[-1.,1.,1.],[1,31,61]))
    assert labels['entry_gain'][0]==pytest.approx(.5)
    assert labels['entry_gain'][1]==pytest.approx(.5)
    assert labels['exit_quality'][0] is None
    assert trades['entry_us'][0]<trades['exit_us'][0]
    assert labels.filter(pl.col('action')=='EXIT')['direction'].to_list()==[1]


def test_unprofitable_and_orphan_short_have_no_reference_or_arrows():
    labels,_,pairs,trades=op.calculate(bars([10.,9.,8.,7.,6.],[-1.,1.,1.,-1.,-1.]))
    assert trades.is_empty()
    assert labels['action'].to_list()==['WAIT']*5
    assert labels['entry_quality'].to_list()==[0.]*5
    assert labels['pair_id'].to_list()==[1,1,1,0,0]
    assert pairs['reference_entry_us'][0] is None


def test_all_short_and_first_long_are_preserved():
    labels,_,pairs,trades=op.calculate(bars([10.,11.],[-1.,-1.]))
    assert pairs.is_empty() and trades.is_empty()
    assert labels.height==2
    _,_,pairs,_=op.calculate(bars([10.,11.],[0.,1.]))
    assert pairs['short_episode'][0] is None


def test_threshold_monotonicity_and_independent_conditional_views():
    labels,_,_,_=op.calculate(bars([10.,9.,9.05,9.1,10.,10.9,11.,10.95],[-1.,-1.,-1.,1.,1.,1.,1.,1.]))
    loose=op.classify(labels,.8)
    strict=op.classify(labels,1.)
    for action in ['ENTRY','EXIT']:
        assert strict.filter(pl.col('action')==action).height<=loose.filter(pl.col('action')==action).height
    flat=op.classify(labels,.9,'flat')
    assert set(flat['action'])<= {'ENTRY','WAIT'}
    held=op.classify(labels,.9,'held')
    assert set(held['action'])<= {'EXIT','HOLD','WAIT'}
    assert op.classify(labels,.9,'reference')['action'].to_list()==labels['reference_action'].to_list()
    with pytest.raises(ValueError,match='threshold'):
        op.classify(labels,0.)


def test_routes_bound_quality_and_view():
    app=FastAPI();app.include_router(router);client=TestClient(app)
    assert client.get('/api/research/models/v6/price-action?quality_threshold=0').status_code==422
    assert client.get('/api/research/models/v6/price-action/chart?quality_threshold=1.1').status_code==422
    assert client.get('/api/research/models/v6/price-action/chart?view=unknown').status_code==422
