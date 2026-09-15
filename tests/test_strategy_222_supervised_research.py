from copy import deepcopy
import json
import numpy as np
import pytest
from scripts.strategy_222_supervised_research import (
    FEATURES,causal_features,hindsight_label,label_opportunities,metrics,flat_entry_state,
)
from scripts.run_strategy_222_refinement import load_recipe


def test_features_reject_future_bars_and_macd_and_have_no_label_fields():
    metadata=dict(macd=dict(observed_at=10,line=.2,signal=.1),liquidity_admission=dict(
        facts=dict(session_dollar_volume=200000,session_share_volume=25000,spread_bps=100,
                   trade_rate_10s=10,trade_rate_60s=5),
        checks=dict(fresh_uncrossed_quote=True,detector_fresh=True)))
    current=dict(at=10,open=10,close=10.2)
    prior=[dict(at=8,low=9.8,high=10.1),dict(at=9,low=9.9,high=10.2)]
    features,stop=causal_features(metadata,current,prior,10)
    assert set(features)==set(FEATURES) and stop==pytest.approx(9.79)
    with pytest.raises(ValueError,match='decision clock'):
        causal_features(metadata,current,[*prior,dict(at=10,low=1,high=20)],10)
    future=deepcopy(metadata);future['macd']['observed_at']=11
    with pytest.raises(ValueError,match='Future MACD'):
        causal_features(future,current,prior,10)


def quotes(prices):
    return np.array([[stamp*1e6,bid,ask,100,100,i] for i,(stamp,bid,ask) in enumerate(prices)],dtype=float)


def test_reentry_cooldown_is_flat_but_pending_orders_and_held_positions_are_not():
    assert flat_entry_state(dict(status='watching'))
    assert flat_entry_state(dict(status='reentry_cooldown'))
    assert not flat_entry_state(dict(status='managing'))
    assert not flat_entry_state(dict(status='entry_pending'))


def test_hindsight_labels_respect_barrier_order_costs_depth_and_censoring():
    q=quotes([(10.1,9.99,10),(15,11.2,11.21),(310,12,12.01)])
    label=hindsight_label(q,10,9.5)
    assert label['profitable'] and label['major_good'] and label['target_hit']
    assert label['entry_ask']>10 and label['exit_bid']<11.2
    stopped=quotes([(10.1,9.99,10),(12,9.4,9.41),(15,11.2,11.21),(310,12,12.01)])
    label=hindsight_label(stopped,10,9.5)
    assert not label['profitable'] and not label['major_good']
    assert label['exit_at']==12
    assert hindsight_label(q[:1],10,9.5)['reason']=='right_censored'
    shallow=q.copy();shallow[1,3]=50
    assert hindsight_label(shallow,10,9.5)['reason']=='exit_quote_or_depth'
    shallow[1,3]=float('nan')
    assert hindsight_label(shallow,10,9.5)['reason']=='exit_quote_or_depth'


def test_future_episode_labels_do_not_mutate_features_or_count_duplicate_entries():
    rows=[dict(window='X',at=t,features=dict(price=p)) for t,p in [(10,10),(20,11),(200,12)]]
    original=deepcopy(rows)
    labels=[dict(major_good=True,profitable=True,entry_ask=p,favorable_return=.2) for p in (10,11,12)]
    label_opportunities(rows,labels)
    assert rows==original
    assert labels[0]['opportunity_id']==labels[1]['opportunity_id']!=labels[2]['opportunity_id']
    result=metrics(np.array([True,True,False]),np.ones(3,dtype=bool),labels,rows)
    assert result['major_opportunities']==2 and result['major_opportunity_coverage']==.5
    assert result['entry_timing_quality']==1


def test_recipe_accepts_only_bounded_parameter_paths(tmp_path):
    path=tmp_path/'recipe.json'
    path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_minimum_body_bps=30)))))
    assert load_recipe(path)['historical_hod']['setup_minimum_body_bps']==30
    path.write_text(json.dumps(dict(parameters=dict(command=dict(shell='anything')))))
    with pytest.raises(ValueError,match='unapproved parameter'):load_recipe(path)
