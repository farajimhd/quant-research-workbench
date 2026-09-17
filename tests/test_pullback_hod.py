from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import pullback_hod as P, strategy_engine as S
from src.trading_runtime.session_relative_volume import CONTRACT as RVOL
from tests.test_v7_setup import prepared


def fixture():
    host,a,obs = prepared()
    p = deepcopy(a.parameters)
    p.update(pullback_hod_contract=P.CONTRACT,pullback_hod=dict(P.DEFAULTS))
    p['historical_hod'].update(setup_minimum_session_relative_volume=2.,setup_minimum_volume_ratio=0.)
    state = deepcopy(a.state)
    state['historical_hod_state']['hod'] = 10.2
    a = replace(a,parameters=p,state=state)
    o = obs(2,10.02)
    now = o.observed_at.timestamp()
    market = deepcopy(o.structural_detector_state)
    market['row']['local_swings'] = [
        dict(side='resistance',price=10.12,lower=10.11,upper=10.13,pivot_at=now-10,confirmed_at=now-8),
        dict(side='support',state='active',price=9.995,lower=9.99,upper=10.,pivot_at=now-3,confirmed_at=now-1)]
    o = replace(o,bar_open=10.,bar_high=10.025,bar_low=9.999,execution_vwap=9.,
        structural_detector_state=market,market_pressure={'session_relative_volume':dict(
            contract=RVOL,observed_at=now,effective_at=int(now),ratio=2.,ready=True,baseline_hash='test')})
    return host,a,o


def test_runnable_entry_closed_candle_and_bounded_execution_envelope():
    host,a,o = fixture()
    r = host.evaluate(a,o)
    intent = next(i for i in r.evaluation.intents if i.action=='enter_long')
    assert intent.reason=='bullish_candle_after_pullback_low'
    assert intent.invalidation_price < 9.99
    assert intent.profit_target_price > 10.2
    envelope = intent.resolved_execution_policy().envelope
    assert not envelope.persist_until_cancelled and envelope.deadline_ms==1000
    assert envelope.maximum_buy_price <= 10.2
    assert r.evaluation.signals[0].metadata['session_relative_volume']['ratio']==2.
    intrabar = replace(o,source_timeframe='',evaluation_events=('market_data_update',))
    assert not host.evaluate(a,intrabar).evaluation.intents


@pytest.mark.parametrize('failure',['future_swing','no_high','red','doji','weak_body','far_from_hod','above_hod','spread','rvol','used_swing'])
def test_entry_rejects_invalid_or_unconfirmed_setups(failure):
    host,a,o = fixture()
    market = deepcopy(o.structural_detector_state)
    if failure=='future_swing': market['row']['local_swings'][1]['confirmed_at']=o.observed_at.timestamp()+1
    if failure=='no_high': market['row']['local_swings']=market['row']['local_swings'][1:]
    if failure=='red': o=replace(o,bar_open=10.03)
    if failure=='doji': o=replace(o,bar_open=o.price)
    if failure=='weak_body': o=replace(o,bar_high=10.2)
    if failure=='far_from_hod': o=replace(o,execution_vwap=10.)
    if failure=='above_hod': a.state['historical_hod_state']['hod']=10.01
    if failure=='spread': o=replace(o,bid=9.8,ask=10.2)
    if failure=='rvol': o.market_pressure['session_relative_volume']['ratio']=1.99
    if failure=='used_swing': a.state['pullback_used_swing']=P.swing_key(market['row']['local_swings'][1])
    assert not host.evaluate(a,replace(o,structural_detector_state=market)).evaluation.intents


def candle(end,opening,high,low,close):
    return dict(time=end-1,end=end,open=opening,high=high,low=low,close=close)


def test_top_requires_rejection_then_later_bearish_confirmation_and_survives_restart():
    active=dict(first_fill_at=100.,setup=dict(hod=10.2))
    market=dict(prior_hod=10.2,prior_rows=[],closed_atr=.04,
                prior_bar=candle(101,10.1,10.15,10.1,10.15),bar=candle(102,10.16,10.21,10.14,10.16))
    assert not P.top_failure(active,market,P.DEFAULTS,.01,False)[0]
    assert not P.top_failure(active,market,P.DEFAULTS,.01,True)[0]
    assert active['top_attempt']
    restored=deepcopy(active)
    market.update(prior_bar=market['bar'],bar=candle(103,10.16,10.18,10.11,10.12))
    assert P.top_failure(restored,market,P.DEFAULTS,.01,True)[0]=='pullback_failed_top'


def test_new_high_disarms_old_rejection_and_gaps_expire_confirmation():
    for gap in (False,True):
        active=dict(first_fill_at=100.,setup=dict(hod=10.2))
        market=dict(prior_hod=10.2,prior_rows=[],closed_atr=.04,prior_bar=candle(101,10.1,10.15,10.1,10.15),
                    bar=candle(102,10.16,10.21,10.14,10.16))
        P.top_failure(active,market,P.DEFAULTS,.01,True)
        market.update(prior_bar=market['bar'],bar=candle(105 if gap else 103,10.16,10.3 if not gap else 10.16,10.1,10.12))
        assert not P.top_failure(active,market,P.DEFAULTS,.01,True)[0]


def test_hod_breakout_is_held_then_failed_close_exits():
    active=dict(first_fill_at=100.,setup=dict(hod=10.2))
    market=dict(prior_hod=10.2,prior_rows=[],closed_atr=.04,bar=candle(102,10.19,10.24,10.19,10.23))
    assert not P.top_failure(active,market,P.DEFAULTS,.01,True)[0]
    market.update(prior_bar=market['bar'],bar=candle(103,10.23,10.24,10.17,10.18))
    assert P.top_failure(active,market,P.DEFAULTS,.01,True)[0]=='pullback_hod_break_failed'


def test_pending_entry_expires_without_session_lockout():
    host,a,o = fixture()
    entered = host.evaluate(a,o)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.ENTRY_PENDING)
    late=replace(o,observed_at=o.observed_at.replace(microsecond=0)+__import__('datetime').timedelta(seconds=1),
                 source_timeframe='',evaluation_events=('market_data_update',))
    result=host.evaluate(a,late)
    assert any(i.action=='cancel_entry' for i in result.evaluation.intents)
    assert result.status==S.AssignmentStatus.WATCHING


def test_config_rejects_unknown_or_nonfinite_thresholds():
    _,a,_=fixture()
    for key,value in [('entry_zone_fraction',1.1),('maximum_swing_age_s',float('nan')),('typo',2)]:
        p=deepcopy(a.parameters);p['pullback_hod'][key]=value
        with pytest.raises(ValueError): S.resolve_long_momentum_parameters(p)


def test_new_confirmed_low_rearms_without_reusing_old_entry():
    host,a,o=fixture()
    entered=host.evaluate(a,o)
    state=deepcopy(a.state)
    used=entered.state['pullback_used_swing']
    state['pullback_used_swing']=used
    a=replace(a,state=state)
    assert not host.evaluate(a,o).evaluation.intents
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings'][1]['pivot_at']+=.5
    r=host.evaluate(a,replace(o,structural_detector_state=market))
    assert any(i.action=='enter_long' for i in r.evaluation.intents)


def test_runnable_failed_top_exits_and_pending_exit_does_not_duplicate():
    from datetime import timedelta
    host,a,o=fixture()
    entered=host.evaluate(a,o)
    state=deepcopy(entered.state)
    now=o.observed_at.timestamp()
    state['pullback_entry']['first_fill_at']=now-3
    state['pullback_entry']['top_attempt']=dict(candle=candle(now,10.07,10.12,10.05,10.07),
                                              level=10.12,minimum_retreat=.02)
    a=replace(a,state=state,status=S.AssignmentStatus.MANAGING)
    o=replace(o,observed_at=o.observed_at+timedelta(seconds=1),position_quantity=100,
        average_price=10.02,bar_open=10.04,bar_high=10.06,bar_low=10.015)
    r=host.evaluate(a,o)
    exit_intent=next(i for i in r.evaluation.intents if i.action=='exit')
    assert exit_intent.reason=='pullback_failed_top'
    assert exit_intent.metadata['cancel_entry_acquisition']
    waiting=host.evaluate(replace(a,state=r.state,status=r.status),replace(o,pending_exit_quantity=100))
    assert not waiting.evaluation.intents


def test_candidate_preserves_source_filters_and_parent():
    from src.backend import pullback_hod_candidate as C
    source=dict(profile_id=C.PARENT_PROFILE,parameters=dict(liquidity_admission={'minimum_price':1.,'maximum_price':20.},
        historical_hod=dict(setup_minimum_session_relative_volume=2.,setup_minimum_volume_ratio=.5)),
        lifecycle=dict(initial_entry=dict(add_steps=[]),reentry={},trading_behavior={'eligible_sessions':['premarket']}))
    baseline=dict(candidate_id=C.BASELINE_ID,content_hash=C.BASELINE_HASH,payload=dict(
        strategy=dict(profiles=[source]),run_plans=dict(plans=[dict(run_plan_id=C.PLAN,allowed_environments=['backtest'])])))
    before=deepcopy(baseline)
    result=C.prepare_payload(baseline,{})
    profile=result['strategy']['profiles'][-1]
    assert baseline==before
    assert profile['parameters']['liquidity_admission']==source['parameters']['liquidity_admission']
    assert profile['lifecycle']['trading_behavior']==source['lifecycle']['trading_behavior']
    assert profile['parameters']['historical_hod']['setup_minimum_session_relative_volume']==2.
    assert profile['parameters']['historical_hod']['setup_minimum_volume_ratio']==.5
    assert result['run_plans']['plans'][0]['allowed_environments']==['backtest']


def rising_fixture():
    host,a,o=fixture()
    a.parameters['pullback_hod_contract']=P.RISE_CONTRACT
    # Initial entry must work without a preceding high or pullback.
    o.structural_detector_state['row']['local_swings']=o.structural_detector_state['row']['local_swings'][1:]
    return host,a,o


def test_rising_initial_entry_does_not_require_pullback_but_v1_stays_unchanged():
    host,a,o=rising_fixture()
    r=host.evaluate(a,o)
    assert next(i for i in r.evaluation.intents if i.action=='enter_long').reason=='initial_swing_rise'
    assert r.state['pullback_entry']['setup']['preceding_high'] is None
    a.parameters['pullback_hod_contract']=P.CONTRACT
    assert host.evaluate(a,o).evaluation.signals[0].reason=='preceding_pullback_high_unavailable'


def test_actual_flat_exit_arms_reentry_not_a_partial_or_unfilled_exit():
    from datetime import datetime,timezone
    state=dict(pullback_entry=dict(confirmed_at=99.,setup=dict(hod=10.2)))
    P.record_exit(state,datetime.fromtimestamp(101,timezone.utc),0)
    assert 'pullback_last_exit' not in state
    active=state['pullback_entry']
    active.update(first_fill_at=100.,advanced=True,advance_peak=dict(price=10.2,at=100.5))
    for remaining in (None,50):
        P.record_exit(state,datetime.fromtimestamp(101,timezone.utc),remaining)
        assert 'pullback_last_exit' not in state
    P.record_exit(state,datetime.fromtimestamp(101,timezone.utc),0)
    assert state['pullback_last_exit']['advanced']
    assert state['pullback_last_exit']['at']==101.


@pytest.mark.parametrize('old_low,deep_enough,allowed',[(False,True,True),(True,True,False),(False,False,False)])
def test_pullback_reentry_requires_new_post_exit_low_and_held_peak(old_low,deep_enough,allowed):
    host,a,o=rising_fixture()
    now=o.observed_at.timestamp()
    a.state['pullback_last_exit']=dict(at=now-2 if old_low else now-4,advanced=True,
        peak=dict(price=10.12 if deep_enough else 10.,at=now-5),entry_at=now-8)
    r=host.evaluate(a,o)
    entries=[i for i in r.evaluation.intents if i.action=='enter_long']
    assert bool(entries)==allowed
    if allowed:
        assert entries[0].reason=='pullback_reentry'
        assert r.state['pullback_entry']['setup']['prior_exit']==a.state['pullback_last_exit']


def test_unadvanced_loss_allows_new_initial_rise_instead_of_global_lockout():
    host,a,o=rising_fixture()
    a.state['pullback_last_exit']=dict(at=o.observed_at.timestamp()-4,advanced=False,peak=None)
    assert next(i for i in host.evaluate(a,o).evaluation.intents if i.action=='enter_long').reason=='initial_swing_rise'


def test_advance_uses_actual_fill_and_excludes_prefill_candle_extreme():
    from types import SimpleNamespace
    from datetime import datetime,timezone
    o=SimpleNamespace(price=10.,average_price=10.,observed_at=datetime.fromtimestamp(101,timezone.utc),
                      evaluation_events=('quote',),changed_source_ids=())
    active={}
    market=dict(bar=candle(101,10.,11.,10.,10.))
    P.observe_advance(active,o,market,.01,True)
    assert 'advanced' not in active
    active['first_fill_at']=100.5
    P.observe_advance(active,o,market,.01,True)
    assert not active.get('advanced')
    o.price=10.02
    P.observe_advance(active,o,market,.01,False)
    assert not active.get('advanced')  # A quote cannot invent a post-fill trade.
    o.evaluation_events=('market_data_update',)
    o.changed_source_ids=('market.last_price',)
    P.observe_advance(active,o,market,.01,False)
    assert active['advanced'] and active['advance_peak']['price']==10.02


def test_rise_candidate_changes_only_entry_contract_and_preserves_v1():
    from src.backend import pullback_hod_candidate as C
    profile=dict(profile_id=C.PROFILE,parameters=dict(pullback_hod_contract=P.CONTRACT,
        pullback_hod=dict(P.DEFAULTS),liquidity_admission=dict(minimum_price=1.,maximum_price=20.)),
        lifecycle=dict(initial_entry=dict(add_steps=[]),trading_behavior=dict(eligible_sessions=['premarket'])))
    baseline=dict(candidate_id=C.RISE_BASELINE_ID,content_hash=C.RISE_BASELINE_HASH,
        payload=dict(strategy=dict(profiles=[profile]),run_plans=dict(plans=[dict(
            run_plan_id=C.PLAN,allowed_environments=['backtest'])])))
    before=deepcopy(baseline)
    payload=C.prepare_rise_payload(baseline,{})
    result=payload['strategy']['profiles'][-1]
    assert baseline==before and payload['strategy']['profiles'][0]==profile
    expected=deepcopy(profile['parameters']);expected['pullback_hod_contract']=P.RISE_CONTRACT
    assert result['parameters']==expected
    assert result['lifecycle']==profile['lifecycle']
    assert payload['run_plans']['plans'][0]['allowed_environments']==['backtest']


def macd_fixture(line=.1,signal=.05):
    from datetime import datetime,timezone
    host,a,o=rising_fixture()
    a.parameters['pullback_hod']['entry_macd_10s_enabled']=1
    boundary=int(o.observed_at.timestamp()//10)*10
    values={f'indicator.macd.{field}@10s':dict(value=value,
        observed_at=datetime.fromtimestamp(boundary,timezone.utc).isoformat())
        for field,value in [('line',line),('signal',signal)]}
    return host,a,replace(o,source_values={**o.source_values,**values})


@pytest.mark.parametrize('reentry',[False,True])
@pytest.mark.parametrize('line,signal,allowed',[(.1,.05,True),(.05,.05,True),(-.1,-.1,True),(-.1,0.,False),(.04,.05,False)])
def test_10s_macd_gates_both_entry_paths_with_equality_allowed(reentry,line,signal,allowed):
    host,a,o=macd_fixture(line,signal)
    if reentry:
        now=o.observed_at.timestamp()
        a.state['pullback_last_exit']=dict(at=now-4,advanced=True,peak=dict(price=10.12,at=now-5),entry_at=now-8)
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==allowed
    assert result.evaluation.signals[0].metadata['entry_macd_10s']['passed']==allowed


@pytest.mark.parametrize('bad',['missing','stale','future','mismatched','naive','nan','wrong_timeframe'])
def test_10s_macd_never_uses_missing_stale_or_other_timeframe_values(bad):
    from datetime import datetime,timezone,timedelta
    host,a,o=macd_fixture()
    values=deepcopy(o.source_values);key='indicator.macd.signal@10s'
    at=datetime.fromisoformat(values[key]['observed_at'])
    if bad=='missing':values.pop(key)
    if bad=='stale':
        for key10 in ('indicator.macd.line@10s','indicator.macd.signal@10s'):
            values[key10]['observed_at']=(at-timedelta(seconds=10)).isoformat()
    if bad=='future':
        for key10 in ('indicator.macd.line@10s','indicator.macd.signal@10s'):
            values[key10]['observed_at']=(at+timedelta(seconds=10)).isoformat()
    if bad=='mismatched':values[key]['observed_at']=(at-timedelta(seconds=10)).isoformat()
    if bad=='naive':values[key]['observed_at']=at.replace(tzinfo=None).isoformat()
    if bad=='nan':values[key]['value']=float('nan')
    if bad=='wrong_timeframe':values={k.replace('@10s','@1s'):v for k,v in values.items()}
    r=host.evaluate(a,replace(o,source_values=values,macd_line=1.,macd_signal=0.))
    assert not r.evaluation.intents
    assert r.evaluation.signals[0].reason=='macd_10s_unavailable'


@pytest.mark.parametrize('partial',[False,True])
def test_10s_macd_cancels_pending_acquisition_without_forcing_position_exit(partial):
    host,a,o=macd_fixture()
    entered=host.evaluate(a,o)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING if partial else S.AssignmentStatus.ENTRY_PENDING)
    values=deepcopy(o.source_values);values['indicator.macd.line@10s']['value']=0.
    o=replace(o,source_values=values,source_timeframe='',evaluation_events=('market_data_update',),
              position_quantity=50 if partial else 0,average_price=10.02 if partial else 0)
    result=host.evaluate(a,o)
    assert any(i.action=='cancel_entry' and i.reason=='macd_10s_below_signal' for i in result.evaluation.intents)
    assert not any(i.action=='exit' for i in result.evaluation.intents)
    if partial:
        again=host.evaluate(replace(a,state=result.state,status=result.status),o)
        assert not any(i.action=='cancel_entry' for i in again.evaluation.intents)


def test_10s_macd_dependencies_are_opt_in_and_gate_boolean_is_validated():
    _,a,_=rising_fixture()
    assert '10s' not in S.strategy_rule_timeframes(a.parameters)
    a.parameters['pullback_hod']['entry_macd_10s_enabled']=1
    assert S.strategy_rule_timeframes(a.parameters)=={'100ms','1s','5s','10s'}
    for bad in (-1,2,'1',float('nan')):
        a.parameters['pullback_hod']['entry_macd_10s_enabled']=bad
        with pytest.raises(ValueError):S.resolve_long_momentum_parameters(a.parameters)
