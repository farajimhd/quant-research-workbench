import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from src.trading_runtime import v7_setup as V, strategy_engine as S
from tests.test_v7_setup import prepared


@pytest.mark.parametrize('low,enters',[(9.5,True),(10.,False)])
def test_initial_range_gate_uses_completed_history_only(low,enters):
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_minimum_300s_range_pct']=5.
    o=obs(2,10.02);at=o.observed_at.timestamp()
    a.state['v7_setup']['range_bars_300s']=[dict(end=at-100,high=10.02,low=low)]
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters
    evidence=result.evaluation.signals[0].metadata['entry_range']
    assert evidence['passed']==enters
    assert evidence['observed_at']==at
    assert evidence['oldest_at']==at-100
    if not enters:assert result.evaluation.signals[0].reason=='setup_range_range_below_minimum'


def test_range_history_expires_exact_boundary_survives_gaps_and_checkpoint():
    settings=dict(setup_range_seconds=30,setup_minimum_bars=5,setup_minimum_300s_range_pct=5.)
    state={}
    def market(end,session='day',episode=1,high=10.02):
        return dict(session=session,episode=episode,
            bar=dict(time=end-1,end=end,open=10,close=10.01,high=high,low=10))
    V.observe(state,market(1,high=11),settings,True)
    state=json.loads(json.dumps(state))
    V.observe(state,market(300,episode=2),settings,True)
    assert V.entry_range(state,market(300),5)['passed']
    assert state['range'] is None  # Short range gaps do not erase the long window.
    unchanged=deepcopy(state)
    V.observe(state,market(301,high=100),settings,False)
    V.observe(state,market(299,high=100),settings,True)
    assert state==unchanged
    V.observe(state,market(301),settings,True)
    evidence=V.entry_range(state,market(301),5)
    assert not evidence['passed']
    assert evidence['oldest_at']==300  # Exactly 300 seconds old is excluded.
    for end in range(302,650):V.observe(state,market(end),settings,True)
    assert len(state['range_bars_300s'])==300
    V.observe(state,market(650,session='next'),settings,True)
    assert len(state['range_bars_300s'])==1
    assert not V.entry_range(state,market(650,session='next'),5)['passed']


def test_range_gate_does_not_block_management_of_an_existing_position():
    host,a,obs=prepared()
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    a.parameters['historical_hod']['setup_minimum_300s_range_pct']=5.
    result=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert all(not s.reason.startswith('setup_range_') for s in result.evaluation.signals)
    assert any(i.action=='add_long' for i in result.evaluation.intents)


@pytest.mark.parametrize('bad',[
    dict(end=301,high=100,low=10),dict(end=0,high=100,low=10),
    dict(end=299,high=float('inf'),low=10),dict(end=299,high=10,low=0),
    dict(end=300,high=100,low=10),
])
def test_range_gate_fails_closed_for_future_expired_or_invalid_history(bad):
    state=dict(range_bars_300s=[bad,dict(end=300,high=10.02,low=10)])
    result=V.entry_range(state,dict(bar=dict(end=300)),5)
    assert not result['passed']
    assert result['reason']=='invalid_completed_history'


def test_range_gate_missing_current_bar_is_explicit():
    result=V.entry_range({},dict(bar=dict(end=300)),5)
    assert not result['passed']
    assert result['reason']=='current_completed_bar_missing'


@pytest.mark.parametrize('reference,enters',[(9.8,True),(10.1,False),(None,False)])
def test_initial_entry_progress_requires_a_fresh_asof_reference(reference,enters):
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_minimum_60s_progress_pct']=1.
    o=obs(2,10.02);at=o.observed_at.timestamp()
    if reference is not None:
        a.state['v7_setup']['progress_bars']=[dict(end=at-60,close=reference)]
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters
    evidence=result.evaluation.signals[0].metadata['entry_progress']
    assert evidence['passed']==enters
    if not enters:assert result.evaluation.signals[0].reason.startswith('setup_progress_')
    if reference is not None:assert evidence['reference_at']==at-60


def test_progress_history_is_bounded_checkpoint_safe_and_separate_from_range_gaps():
    state={};settings=dict(setup_range_seconds=30,setup_minimum_bars=5,setup_minimum_60s_progress_pct=1)
    def market(end,session='day'):
        return dict(session=session,episode=1,bar=dict(time=end-1,end=end,open=10,close=10.2,high=10.2,low=10))
    V.observe(state,market(35),settings,True)
    state=json.loads(json.dumps(state))
    V.observe(state,market(41),settings,True)
    V.observe(state,market(100),settings,True)
    assert V.entry_progress(state,market(100),1)['reference_at']==35
    assert state['range'] is None
    unchanged=deepcopy(state)
    V.observe(state,market(101),settings,False)
    V.observe(state,market(99),settings,True)
    assert state==unchanged
    V.observe(state,market(107),settings,True)
    assert V.entry_progress(state,market(107),1)['reason']=='historical_reference_missing_or_stale'
    for end in range(108,300):V.observe(state,market(end),settings,True)
    assert len(state['progress_bars'])==66
    V.observe(state,market(300,'next'),settings,True)
    assert len(state['progress_bars'])==1
    assert not V.entry_progress(state,market(300,'next'),1)['passed']


@pytest.mark.parametrize('maximum,allowed',[(1.,True),(.25,False)])
def test_add_wick_gate_consumes_rejected_confirmation_without_late_replay(maximum,allowed):
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_add_maximum_upper_wick_fraction']=maximum
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.19),position_quantity=100,average_price=10.02,
        bar_open=10.02,bar_high=10.29,bar_low=10.)
    result=host.evaluate(a,o)
    assert any(i.action=='add_long' for i in result.evaluation.intents)==allowed
    if not allowed:
        assert not result.evaluation.signals[0].metadata['add_candle_quality']['passed']
        assert not result.state['historical_hod_entry'].get('add_breaks')
        a=replace(a,state=result.state)
        later=host.evaluate(a,replace(obs(4,10.20),position_quantity=100,average_price=10.02))
        assert not any(i.action=='add_long' for i in later.evaluation.intents)


@pytest.mark.parametrize('key,value',[
    ('setup_minimum_300s_range_pct',-1),('setup_minimum_300s_range_pct',True),
    ('setup_minimum_300s_range_pct',float('inf')),('setup_minimum_300s_range_pct',float('nan')),
    ('setup_minimum_60s_progress_pct',-1),('setup_minimum_60s_progress_pct',True),
    ('setup_minimum_60s_progress_pct',float('inf')),('setup_minimum_60s_progress_pct',float('nan')),
    ('setup_add_maximum_upper_wick_fraction',-1),('setup_add_maximum_upper_wick_fraction',1.01),
    ('setup_add_maximum_upper_wick_fraction',True),('setup_add_maximum_upper_wick_fraction',float('nan')),
])
def test_setup_noise_filters_reject_invalid_parameters(key,value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared();a.parameters['historical_hod'][key]=value
    with pytest.raises(ValueError):configure(a.parameters)


@pytest.mark.parametrize('enabled,bid,ask,enters', [
    (0,9.99,10.03,True),  # Retain the selected baseline's trade-price policy.
    (1,9.99,10.03,False), # Only one cent remains before the 9.98 stop.
    (1,10.00,10.02,True), # Exactly one spread of executable clearance.
    (1,10.01,10.03,True),
    (1,9.97,10.02,False), # Stop above bid is never acceptable when enabled.
    (2,10.00,10.02,False),
])
def test_setup_quote_clearance_uses_real_bid_without_moving_structural_stop(enabled,bid,ask,enters):
    host,a,obs=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod']['setup_minimum_quote_clearance_spreads']=enabled
    result=host.evaluate(replace(a,parameters=parameters),replace(obs(2,10.02),bid=bid,ask=ask))
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters
    if enabled:
        evidence=result.evaluation.signals[0].metadata['entry_quote_clearance']
        assert evidence['bid']==bid and evidence['ask']==ask
        assert evidence['stop']==pytest.approx(9.98)
        assert evidence['minimum']==pytest.approx(enabled*(ask-bid))
    if enters:
        assert result.state['initial_stop']==pytest.approx(9.98)
    else:
        assert result.evaluation.signals[0].reason=='setup_stop_inside_quote_noise'
        assert not result.state.get('historical_hod_entry')


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf'),True])
def test_setup_quote_clearance_rejects_invalid_configuration(value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod']['setup_minimum_quote_clearance_spreads']=value
    with pytest.raises(ValueError):
        configure(parameters)


def test_setup_quote_clearance_requires_setup_policy():
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod'].update(v7_setup_enabled=0,setup_minimum_quote_clearance_spreads=1)
    with pytest.raises(ValueError,match='Setup quality policies require early setup entry'):
        configure(parameters)


def test_episode_high_is_prior_only_and_resets_with_episode():
    state={};settings=dict(setup_range_seconds=30,setup_minimum_bars=1)
    for i,episode,high in [(1,1,10),(2,1,11),(3,2,9)]:
        V.observe(state,dict(session='day',episode=episode,bar=dict(time=i-1,end=i,
            open=high-.1,close=high,high=high,low=high-.2)),settings,True)
        assert state['prior_episode_high']==(10 if i==2 else None)
    assert state['episode_high']==9


@pytest.mark.parametrize('price,opening,enters',[(10.02,10,False),(10.1,10,False),
    (10.11,10,True),(10.11,10.12,False)])
def test_episode_high_entry_replaces_below_range_requirement(price,opening,enters):
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod']['setup_episode_high_entry']=1
    state=deepcopy(a.state)
    state['historical_hod_state']['episode']=obs(2,price).observed_at.timestamp()-5
    state['v7_setup'].update(episode=state['historical_hod_state']['episode'],episode_high=10.1)
    a=replace(a,parameters=p,state=state)
    result=host.evaluate(a,replace(obs(2,price),bar_open=opening,bar_high=max(price,10.12)))
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters,result.evaluation.signals[0].reason
    if enters:
        assert result.state['historical_hod_entry']['breakout_confirmation']['threshold']==10.1
        assert result.evaluation.signals[0].reason=='v7_episode_high_entry'


@pytest.mark.parametrize('minimum,opening,enters', [(0,10.02,True),(5,10.02,False),
    (5,10.0193,False),(5,10.01,True),(10,10.01,False),(10,10.0,True)])
def test_completed_entry_body_threshold_is_independent_of_resistance_buffers(minimum,opening,enters):
    host,a,obs=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod']['setup_minimum_body_bps']=minimum
    result=host.evaluate(replace(a,parameters=parameters),replace(obs(2,10.02),bar_open=opening))
    assert any(i.action=='enter_long' for i in result.evaluation.intents)==enters
    if not enters:
        assert result.evaluation.signals[0].reason=='setup_body_below_minimum'


@pytest.mark.parametrize('defer',[False,True])
def test_optional_trail_wait_keeps_initial_stop_and_activates_on_range_break(defer):
    host,a,obs=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod']['setup_trail_requires_breakout']=int(defer)
    a=replace(a,parameters=parameters)
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    initial=a.state['active_stop']
    for i,price,lower in [(3,10.06,10.02),(4,10.13,10.04)]:
        o=replace(obs(i,price),position_quantity=100,average_price=10.02)
        market=deepcopy(o.structural_detector_state)
        now=o.observed_at.timestamp()
        market['row']['local_swings']=[dict(side='support',lower=lower,price=lower+.002,
            upper=lower+.005,pivot_at=now-1,confirmed_at=now)]
        result=host.evaluate(a,replace(o,structural_detector_state=market))
        stops=[v for v in result.evaluation.intents if v.action=='replace_protective_stop']
        if i==3 and defer:
            assert not stops and result.state['active_stop']==initial
            assert result.state['historical_hod_entry']['setup']['phase']=='building'
        else:
            assert len(stops)==1
            assert result.state['active_stop']==pytest.approx(lower-.01)
        a=replace(a,state=result.state)


def test_position_recovery_peak_survives_macd_episode_reset_and_missing_body():
    state={};entry=dict(confirmed_at=1,setup=dict(phase='post_breakout',breakout_threshold=10.2))
    market=dict(body_high=12,bar=dict(end=5,low=10.3,close=11.5))
    o=SimpleNamespace(position_quantity=100,observed_at=datetime.fromtimestamp(5,timezone.utc))
    for body in (12,11,None):
        market['body_high']=body
        V.recovery_observe(state,entry,market,o,10.4,{},False,preserve_peak=True)
    o.position_quantity=0
    V.recovery_observe(state,{},market,o,10.4,{},False,preserve_peak=True)
    assert state['last_exit']['body_high']==12
    swing=dict(scale='local',pivot_at=6,confirmed_at=7,lower=10.1)
    assert V.recovery_permission(state,swing,market)[0]=='waiting_for_post_move_recovery_or_higher_base'


def test_initial_failure_is_buffered_completed_red_and_expires():
    entry=dict(confirmed_at=10,setup=dict(phase='building',entry_bar=dict(open=10,close=10.1,low=9.99)))
    settings=dict(setup_failure_seconds=3,setup_failure_buffer_ticks=1)
    market=dict(contiguous=True,bar=dict(end=12,open=10,close=9.98))
    assert V.entry_failure(entry,market,settings,.01,True)['threshold']==pytest.approx(9.98)
    assert not V.entry_failure(entry,market,settings,.01,False)
    for bar in [dict(end=12,open=9.97,close=9.98),dict(end=14,open=10,close=9.97),dict(end=12,open=10,close=9.981)]:
        assert not V.entry_failure(entry,dict(market,bar=bar),settings,.01,True)
    assert not V.entry_failure(entry,dict(market,contiguous=False),settings,.01,True)
    entry['setup']['phase']='post_breakout'
    assert not V.entry_failure(entry,market,settings,.01,True)


def test_initial_failure_exit_remembers_reclaim_even_when_next_observation_is_flat():
    host,a,obs=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod'].update(setup_failure_seconds=3,setup_recovery_enabled=1)
    a=replace(a,parameters=parameters)
    entered=host.evaluate(a,replace(obs(2,10.02),bar_low=10.01))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.0),bar_open=10.02,position_quantity=100,average_price=10.02)
    result=host.evaluate(a,o)
    assert any(i.action=='exit' and i.reason=='early_setup_failed' for i in result.evaluation.intents)
    market=result.state['historical_hod_state']
    active=result.state['historical_hod_entry']
    state=result.state['v7_setup']
    V.recovery_observe(state,active,market,replace(o,position_quantity=0),result.state['active_stop'],{},False)
    boundary=state['last_exit']['setup']['entry_failure_recovery']
    assert boundary==pytest.approx(10.03)
    now=o.observed_at.timestamp()
    swing=dict(scale='local',pivot_at=now+1,confirmed_at=now+2,lower=9.99)
    assert V.recovery_permission(state,swing,dict(bar=dict(close=10.02)))[0]=='waiting_for_failed_setup_reclaim'
    assert V.recovery_permission(state,swing,dict(bar=dict(close=10.04)))==('', 'building')


def test_risk_trailing_requires_frozen_fill_risk_and_favorable_completed_close():
    settings=dict(setup_trail_activation_r=.5)
    entry=dict(initial_fill_price=10,initial_risk=.2,best_close=10.099)
    assert not V.risk_trail_ready(entry,settings)
    entry['best_close']=10.1
    assert V.risk_trail_ready(entry,settings)
    assert not V.risk_trail_ready(entry,dict(setup_trail_activation_r=0))
    assert not V.risk_trail_ready(dict(entry,initial_fill_price=0),settings)
    assert not V.risk_trail_ready(dict(entry,initial_risk=-.1),settings)


@pytest.mark.parametrize('stop_hit',[False,True])
def test_failure_observation_preserves_reclaim_without_forcing_exit(stop_hit):
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_failure_seconds=3,
        setup_recovery_enabled=1,setup_failure_exit_enabled=0)
    entered=host.evaluate(a,replace(obs(2,10.02),bar_low=10.01))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    if stop_hit:a.state['active_stop']=10.005
    o=replace(obs(3,10.),bar_open=10.02,position_quantity=100,average_price=10.02)
    result=host.evaluate(a,o)
    exits=[i for i in result.evaluation.intents if i.action=='exit']
    assert [i.reason for i in exits]==(['protective_stop'] if stop_hit else [])
    state=json.loads(json.dumps(result.state['v7_setup']))
    assert state['held']['setup']['entry_failure_recovery']==pytest.approx(10.03)
    # A later flat observation and checkpoint round trip retain the failed
    # entry context, even though this policy did not force the early exit.
    V.recovery_observe(state,{},result.state['historical_hod_state'],
        replace(o,position_quantity=0),result.state['active_stop'],{},False)
    now=o.observed_at.timestamp()
    fresh=dict(scale='local',pivot_at=now+1,confirmed_at=now+2,lower=9.99)
    assert V.recovery_permission(state,fresh,dict(bar=dict(close=10.02)))[0]=='waiting_for_failed_setup_reclaim'
    assert V.recovery_permission(state,fresh,dict(bar=dict(close=10.04)))==('', 'building')


@pytest.mark.parametrize('value',[-1,2,.5,float('nan'),float('inf'),True])
def test_failure_exit_switch_rejects_invalid_values(value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod']['setup_failure_exit_enabled']=value
    with pytest.raises(ValueError,match='numeric boolean switch'):
        configure(a.parameters)


def test_observation_only_failure_requires_recovery():
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod'].update(setup_failure_exit_enabled=0,setup_recovery_enabled=0)
    with pytest.raises(ValueError,match='persistent setup recovery'):
        configure(a.parameters)


def test_half_r_can_activate_swing_trailing_before_full_range_breakout():
    host,a,obs=prepared()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod'].update(setup_trail_requires_breakout=1,setup_trail_activation_r=.5)
    a=replace(a,parameters=parameters)
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.06),position_quantity=100,average_price=10.02)
    market=deepcopy(o.structural_detector_state);now=o.observed_at.timestamp()
    market['row']['local_swings']=[dict(side='support',lower=10.02,price=10.022,upper=10.025,
        pivot_at=now-1,confirmed_at=now)]
    result=host.evaluate(a,replace(o,structural_detector_state=market))
    assert result.state['historical_hod_entry']['setup']['phase']=='building'
    assert any(i.action=='replace_protective_stop' for i in result.evaluation.intents)
    later=host.evaluate(replace(a,state=result.state),replace(obs(4,10.07),position_quantity=200,average_price=10.04))
    assert later.state['historical_hod_entry']['initial_fill_price']==10.02
    assert later.state['historical_hod_entry']['initial_risk']==pytest.approx(.04)


def test_minimum_trail_progress_applies_after_range_break_and_keeps_initial_protection():
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod']['setup_minimum_trail_progress_r']=1.
    state=deepcopy(a.state)
    for bar in state['v7_setup']['bars']:bar['high']=10.025
    a=replace(a,parameters=p,state=state)
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.04),position_quantity=100,average_price=10.02)
    market=deepcopy(o.structural_detector_state);now=o.observed_at.timestamp()
    market['row']['local_swings']=[dict(side='support',lower=10.01,price=10.012,upper=10.015,
        pivot_at=now-1,confirmed_at=now)]
    result=host.evaluate(a,replace(o,structural_detector_state=market))
    assert result.state['historical_hod_entry']['setup']['phase']=='post_breakout'
    assert result.state['active_stop']==pytest.approx(9.98)
    assert not any(i.action=='replace_protective_stop' for i in result.evaluation.intents)
    intrabar=host.evaluate(replace(a,state=result.state),replace(obs(4,10.08),position_quantity=100,
        average_price=10.02,evaluation_events=('market_data_update',)))
    assert intrabar.state['active_stop']==pytest.approx(9.98)
    stopped=host.evaluate(replace(a,state=result.state),replace(obs(4,9.97),position_quantity=100,average_price=10.02))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)
    later=replace(obs(4,10.06),position_quantity=100,average_price=10.02)
    market=deepcopy(later.structural_detector_state)
    market['row']['local_swings']=[dict(side='support',lower=10.01,price=10.012,upper=10.015,
        pivot_at=now,confirmed_at=now+1)]
    trailed=host.evaluate(replace(a,state=result.state),replace(later,structural_detector_state=market))
    assert any(i.action=='replace_protective_stop' for i in trailed.evaluation.intents)


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf'),True])
def test_minimum_trail_progress_rejects_invalid_values(value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    p=deepcopy(a.parameters);p['historical_hod']['setup_minimum_trail_progress_r']=value
    with pytest.raises(ValueError):configure(p)


def test_minimum_trail_progress_requires_actual_fill_and_positive_initial_risk():
    assert not V.risk_progress_ready(dict(initial_risk=.25,best_close=7),.5)
    assert not V.risk_progress_ready(dict(initial_fill_price=6,initial_risk=0,best_close=7),.5)
    assert not V.risk_progress_ready(dict(initial_fill_price=6,initial_risk=.25,best_close=6.12),.5)
    assert V.risk_progress_ready(dict(initial_fill_price=6,initial_risk=.25,best_close=6.125),.5)


@pytest.mark.parametrize('pending',[False,True])
@pytest.mark.parametrize('enabled,requires_bid,price,bid,allowed',[
    (0,1,10.04,10.03,True),(1,1,10.04,10.03,False),
    (1,1,10.06,10.04,False),(1,1,10.06,10.05,True),
    (1,0,10.06,10.04,True),(1,0,10.04,10.03,False),
    (1,0,10.05,10.04,True)])
def test_current_gain_guards_new_and_pending_swing_stops(pending,enabled,requires_bid,price,bid,allowed):
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_trail_requires_current_gain=enabled,
        setup_trail_current_gain_requires_bid=requires_bid,setup_minimum_trail_progress_r=.5)
    for bar in a.state['v7_setup']['bars']:bar['high']=10.025
    entered=host.evaluate(a,obs(2,10.02))
    state=deepcopy(entered.state)
    state['historical_hod_entry']['best_close']=10.2
    o=replace(obs(3,price),position_quantity=100,average_price=10.05,bid=bid)
    market=deepcopy(o.structural_detector_state);now=o.observed_at.timestamp()
    swing=dict(side='support',lower=10.01,price=10.012,upper=10.015,
        pivot_at=now-1,confirmed_at=now)
    market['row']['local_swings']=[swing]
    if pending:
        state['historical_hod_entry'].update(desired_stop=10.,stop_swing=swing)
    a=replace(a,state=json.loads(json.dumps(state)),status=S.AssignmentStatus.MANAGING)
    result=host.evaluate(a,replace(o,structural_detector_state=market))
    assert any(i.action=='replace_protective_stop' for i in result.evaluation.intents)==allowed
    if not allowed:
        assert result.state['active_stop']==pytest.approx(9.98)
        stopped=host.evaluate(replace(a,state=result.state),
            replace(obs(4,9.97),position_quantity=100,average_price=10.05))
        assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)


@pytest.mark.parametrize('value',[-1,2,float('nan'),float('inf'),True])
@pytest.mark.parametrize('setting',['setup_trail_requires_current_gain','setup_trail_current_gain_requires_bid'])
def test_current_gain_switch_validation(value,setting):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod'][setting]=value
    with pytest.raises(ValueError):configure(a.parameters)


def test_phase_progress_requires_actual_fill_completed_green_close_and_survives_restart():
    entry=dict(confirmed_at=1,initial_fill_price=6.78,initial_risk=.42,
        setup=dict(phase='building',breakout_threshold=6.72))
    market=dict(bar=dict(end=2,open=6.75,close=6.88))
    assert not V.phase(entry,market,True,.5)
    assert entry['setup']['phase']=='building'
    assert entry['setup']['phase_progress']['threshold']==pytest.approx(6.99)
    entry=json.loads(json.dumps(entry))
    market['bar']=dict(end=3,open=7.1,close=7.)
    assert not V.phase(entry,market,True,.5)  # Red close cannot activate.
    market['bar']=dict(end=4,open=6.95,close=6.99)
    before=deepcopy(entry)
    assert not V.phase(entry,market,False,.5)
    assert entry==before
    assert V.phase(entry,market,True,.5)
    assert entry['setup']['breakout_at']==4
    assert not V.phase(entry,dict(bar=dict(end=5,open=6.9,close=6.8)),True,.5)
    assert entry['setup']['phase']=='post_breakout'
    unknown=dict(confirmed_at=1,setup=dict(phase='building',breakout_threshold=6.72))
    assert not V.phase(unknown,market,True,.5)


def test_phase_progress_defers_management_but_not_initial_stop():
    host,a,obs=prepared()
    a.parameters['historical_hod']['setup_phase_minimum_progress_r']=1.
    for bar in a.state['v7_setup']['bars']:bar['high']=10.025
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    first=host.evaluate(a,replace(obs(3,10.04),position_quantity=100,average_price=10.02))
    assert first.state['historical_hod_entry']['setup']['phase']=='building'
    stopped=host.evaluate(replace(a,state=first.state),
        replace(obs(4,9.97),position_quantity=100,average_price=10.02))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in stopped.evaluation.intents)
    activated=host.evaluate(replace(a,state=first.state),
        replace(obs(4,10.07),position_quantity=100,average_price=10.02))
    assert activated.state['historical_hod_entry']['setup']['phase']=='post_breakout'


@pytest.mark.parametrize('value',[-1,float('nan'),float('inf'),True])
def test_phase_progress_validation(value):
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod']['setup_phase_minimum_progress_r']=value
    with pytest.raises(ValueError):configure(a.parameters)


@pytest.mark.parametrize('gate,allowed',[(0,True),(1,False)])
def test_add_gate_respects_position_progress_phase(gate,allowed):
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_recovery_enabled=1,
        setup_add_requires_range_breakout=gate,setup_phase_minimum_progress_r=.5)
    entered=host.evaluate(a,obs(2,10.02))
    state=deepcopy(entered.state)
    # Freeze an actual filled position whose range can break before0.5R.
    state['historical_hod_entry'].update(initial_fill_price=10.02,
        initial_risk=.5,fill_risk_frozen=True)
    a=replace(a,state=state,status=S.AssignmentStatus.MANAGING)
    result=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert result.state['historical_hod_entry']['setup']['phase']=='building'
    assert result.state['historical_hod_entry']['setup']['phase_progress']['threshold']==pytest.approx(10.27)
    assert any(i.action=='add_long' for i in result.evaluation.intents)==allowed
