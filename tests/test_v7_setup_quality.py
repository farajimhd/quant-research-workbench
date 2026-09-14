from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from src.trading_runtime import v7_setup as V, strategy_engine as S
from tests.test_v7_setup import prepared


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
