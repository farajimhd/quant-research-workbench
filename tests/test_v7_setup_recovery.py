from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timezone
from src.trading_runtime import v7_setup as V, strategy_engine as S
from tests.test_v7_setup import prepared


def swing(pivot, lower):
    return dict(scale='local',side='support',pivot_at=pivot,confirmed_at=pivot+1,lower=lower,price=lower+.01,upper=lower+.02)


def test_recovery_persists_failure_and_requires_fresh_support():
    state={}; entry=dict(confirmed_at=1,setup=dict(phase='post_breakout',breakout_threshold=10.2))
    market=dict(body_high=11,bar=dict(end=5,low=10.3,close=10.6))
    o=SimpleNamespace(position_quantity=100,observed_at=datetime.fromtimestamp(5,timezone.utc))
    V.recovery_observe(state,entry,market,o,10.4,{},False)
    o.position_quantity=0
    V.recovery_observe(state,{},market,o,10.4,{},False)
    assert V.recovery_permission(state,swing(4,10.5),market)[0]=='waiting_for_new_support_after_exit'
    assert V.recovery_permission(state,swing(6,10.5),market)==('', 'building')
    assert V.recovery_permission(state,swing(6,10.1),market)[0]=='waiting_for_post_move_recovery_or_higher_base'
    market['bar']['close']=11.1
    assert V.recovery_permission(state,swing(6,10.1),market)==('', 'post_breakout')


def test_breached_anchor_cannot_be_reused_even_if_detector_still_active():
    state={}; broken=swing(1,10)
    market=dict(bar=dict(end=5,low=9.99))
    o=SimpleNamespace(position_quantity=0,observed_at=datetime.fromtimestamp(5,timezone.utc))
    row=V.recovery_observe(state,{},market,o,0,dict(local_swings=[broken]),True)
    assert not row['local_swings']
    assert V.swing_key(broken) in state['retired_swings']


def test_setup_add_waits_for_range_breakout():
    host,a,obs=prepared()
    p=deepcopy(a.parameters);p['historical_hod']['setup_recovery_enabled']=1
    a=replace(a,parameters=p)
    # Keep the setup ceiling above the first add level.
    for b in a.state['v7_setup']['bars']:b['high']=10.5
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)
    assert r.state['historical_hod_entry']['setup']['phase']=='building'


def test_recovery_can_retain_individual_resistance_adds_before_range_breakout():
    host,a,obs=prepared()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(setup_recovery_enabled=1,setup_add_requires_range_breakout=0)
    a=replace(a,parameters=p)
    for b in a.state['v7_setup']['bars']:b['high']=10.5
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(obs(3,10.19),position_quantity=100,average_price=10.02))
    assert any(i.action=='add_long' for i in r.evaluation.intents)
    assert r.state['historical_hod_entry']['setup']['phase']=='building'
