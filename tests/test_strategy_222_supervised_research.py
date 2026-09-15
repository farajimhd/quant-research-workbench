from copy import deepcopy
import json
import numpy as np
import pytest
from scripts.strategy_222_supervised_research import (
    FEATURES,causal_features,hindsight_label,label_opportunities,metrics,flat_entry_state,diagnostic_screens,diagnose_episode_exit,completed_close_progress,
)
from scripts.run_strategy_222_refinement import load_recipe, prepare, PROFILE, PLAN


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


def test_progress_uses_asof_reference_and_is_invariant_to_future_bars():
    rows=[dict(symbol='X',at=100)]
    bars={'X':[dict(timeframe='1s',at=t,close=p) for t,p in [(35,10),(41,20),(100,10.2)]]}
    original=deepcopy(bars)
    result=completed_close_progress(rows,bars)
    assert result==[dict(valid=True,reference_at=35.,reference_age_seconds=65.,progress_pct=pytest.approx(2.))]
    assert bars==original
    bars['X'].append(dict(timeframe='1s',at=101,close=1000))
    assert completed_close_progress(rows,bars)==result
    # The 41-second bar is after the target reference clock of40; it must
    # never replace an absent or stale as-of reference.
    bars['X'][0]['at']=34
    assert completed_close_progress(rows,bars)==[dict(valid=False,reason='historical_reference_missing_or_stale')]
    assert completed_close_progress([dict(symbol='X',at=99)],bars)==[dict(valid=False,reason='current_completed_bar_missing')]


@pytest.mark.parametrize('times,prices',[
    ([40,40,100],[10,10,11]),([40,39,100],[10,10,11]),
    ([40,100],[0,11]),([40,100],[float('nan'),11]),
])
def test_progress_rejects_ambiguous_or_invalid_bar_authority(times,prices):
    bars={'X':[dict(timeframe='1s',at=t,close=p) for t,p in zip(times,prices)]}
    with pytest.raises(ValueError):completed_close_progress([dict(symbol='X',at=100)],bars)


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


def test_exit_diagnosis_separates_actual_loss_from_fixed_stop_label():
    episode=dict(opened_at='1970-01-01T00:00:10+00:00',net=-25.,
        fills=[dict(side='B',stop=9.5),dict(side='S',reason='early_setup_failed')])
    q=quotes([(10.1,9.99,10),(15,11.2,11.21),(310,12,12.01)])
    result=diagnose_episode_exit(episode,q)
    assert result['management_or_sizing_review'] and result['actual_net']==-25
    assert result['net']>0 and result['actual_exit_reasons']==['early_setup_failed']
    # Later upside cannot reverse an initial-stop hit.
    stopped=quotes([(10.1,9.99,10),(12,9.4,9.41),(15,11.2,11.21),(310,12,12.01)])
    assert not diagnose_episode_exit(episode,stopped)['management_or_sizing_review']
    episode.pop('net')
    assert not diagnose_episode_exit(episode,q)['management_or_sizing_review']
    assert diagnose_episode_exit(episode,q[:1])['reason']=='right_censored'


def test_exit_diagnosis_requires_actual_initial_stop_metadata():
    episode=dict(opened_at='1970-01-01T00:00:10+00:00',net=-25.,fills=[dict(side='B')])
    q=quotes([(10.1,9.99,10),(15,11.2,11.21),(310,12,12.01)])
    for invalid in (None,True,-1,float('nan')):
        episode['fills'][0]['stop']=invalid
        assert diagnose_episode_exit(episode,q)['reason']=='initial_stop_metadata_unavailable'


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
    path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_minimum_300s_range_pct=4.5)))))
    assert load_recipe(path)['historical_hod']['setup_minimum_300s_range_pct']==4.5
    path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_phase_minimum_progress_r=1.)))))
    assert load_recipe(path)['historical_hod']['setup_phase_minimum_progress_r']==1.
    path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_range_seconds=15)))))
    assert load_recipe(path)['historical_hod']['setup_range_seconds']==15
    for invalid in (-1, float('nan'), float('inf'), True, '1'):
        path.write_text(json.dumps(dict(parameters=dict(historical_hod=dict(setup_phase_minimum_progress_r=invalid)))))
        with pytest.raises(ValueError,match='Invalid research parameter'):load_recipe(path)
    path.write_text(json.dumps(dict(parameters=dict(command=dict(shell='anything')))))
    with pytest.raises(ValueError,match='unapproved parameter'):load_recipe(path)


def test_recipe_preserves_selected_version_and_changes_only_requested_parameter(monkeypatch):
    from src.backend import trading_configuration_service as service
    original=dict(profile_id=PROFILE,parameters=dict(
        historical_hod=dict(unrelated_setting=123),liquidity_admission={},execution=dict(tick_size=.01)))
    payload=dict(strategy=dict(profiles=[original]),run_plans=dict(plans=[dict(run_plan_id=PLAN)]),
                 canvas=dict(revision=1,profile='fixture'))
    frozen=deepcopy(payload)
    monkeypatch.setattr(service,'configuration_candidate',lambda *a,**kw:dict(payload=payload,
        content_hash='d52acbd89292aa01d0dad9f760632006a0ee9c35bdccc7d81e70e6e941e994e3'))
    monkeypatch.setattr(service,'configuration_base',lambda:dict(strategy=dict(profiles=[])))
    monkeypatch.setattr(service,'create_test_candidate',lambda **kw:kw)

    baseline=prepare('regular-origin-v31')
    trial=prepare('range-experiment',{'historical_hod':{'setup_minimum_300s_range_pct':4.5}},'regular-origin-v31')
    expected=deepcopy(baseline['configuration']['strategy']['profiles'][-1]['parameters'])
    expected['historical_hod']['setup_minimum_300s_range_pct']=4.5
    actual=trial['configuration']['strategy']['profiles'][-1]
    assert actual['parameters']==expected
    assert actual['parameters']['historical_hod']['setup_recovery_regular_full_range']==1
    assert actual['parameters']['historical_hod']['setup_trail_current_gain_requires_bid']==0
    assert actual['parameters']['liquidity_admission']['minimum_current_trade_rate_60s']==2
    assert trial['strategy_profile_id']==actual['profile_id']=='v7-222-range-experiment'
    assert trial['configuration']['run_plans']['plans'][0]['profile_id']==actual['profile_id']
    assert payload==frozen

    phase_trial=prepare('phase-experiment',{'historical_hod':{'setup_phase_minimum_progress_r':1.}},'regular-origin-v31')
    expected_phase=deepcopy(baseline['configuration']['strategy']['profiles'][-1]['parameters'])
    expected_phase['historical_hod']['setup_phase_minimum_progress_r']=1.
    assert phase_trial['configuration']['strategy']['profiles'][-1]['parameters']==expected_phase
    range_trial=prepare('short-base-experiment',{'historical_hod':{'setup_range_seconds':15}},'regular-origin-v31')
    expected_range=deepcopy(baseline['configuration']['strategy']['profiles'][-1]['parameters'])
    expected_range['historical_hod']['setup_range_seconds']=15
    assert range_trial['configuration']['strategy']['profiles'][-1]['parameters']==expected_range
    assert payload==frozen

    legacy=prepare('legacy-experiment',{'historical_hod':{'setup_minimum_body_bps':30}})
    explicit=prepare('legacy-experiment',{'historical_hod':{'setup_minimum_body_bps':30}},'full-v6')
    assert legacy==explicit
    assert legacy['configuration']['strategy']['profiles'][-1]['parameters']['liquidity_admission']['minimum_current_trade_rate_60s']==3
    with pytest.raises(ValueError,match='requires parameter overrides'):
        prepare('invalid',recipe_base='regular-origin-v31')
    with pytest.raises(ValueError,match='Unsupported'):
        prepare('invalid',{},'unknown')


def test_diagnostics_expose_lost_and_delayed_opportunities_without_mutating_labels():
    features={key:1. for key in FEATURES}
    features.update(fresh_quote=True,detector_fresh=True,liquidity_facts_fresh=True,
        post_exit=False,session_dollars=200000,session_shares=25000,spread_bps=100,
        body_bps=30,rate10=6,rate60=3)
    rows=[dict(id=str(i),symbol=s,at=t,features=dict(features,prior_range_pct=width))
          for i,(s,t,width) in enumerate([('X',10,1),('X',20,4),('Y',10,1),('Z',10,5)])]
    labels=[dict(id=str(i),major_good=i<3,profitable=i<3,opportunity_id=key,
                 timing_quality=1,entry_ask=10+i)
            for i,key in enumerate(['X:10','X:10','Y:10','Z:10'])]
    original=deepcopy((rows,labels))
    result=next(r for r in diagnostic_screens(rows,labels)
                if r['feature']=='prior_range_pct' and r['minimum']==3)
    assert result['metrics']['admitted_rows']==2
    assert result['metrics']['profitable_fraction']==.5
    assert result['metrics']['major_opportunity_coverage']==.5
    assert [r['symbol'] for r in result['lost_opportunities']]==['Y']
    assert result['delayed_opportunities'][0]['delay_seconds']==10
    assert result['delayed_opportunities'][0]['filtered_ask']==11
    assert (rows,labels)==original
    with pytest.raises(ValueError,match='misaligned'):
        diagnostic_screens(rows,labels[::-1])
