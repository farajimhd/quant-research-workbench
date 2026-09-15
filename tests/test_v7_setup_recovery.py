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
    assert V.recovery_permission(state,swing(6,10.1),market)==('', 'building')
    # Reclaiming a previous trade cannot activate rejection exits below the
    # new position's own range high.
    recovered=dict(confirmed_at=6,setup=dict(phase='building',breakout_threshold=11.))
    assert not V.phase(recovered,dict(bar=dict(end=7,open=10.7,close=10.8)),True)
    assert V.phase(recovered,dict(bar=dict(end=8,open=10.9,close=11.1)),True)


def test_breached_anchor_cannot_be_reused_even_if_detector_still_active():
    state={}; broken=swing(1,10)
    market=dict(bar=dict(end=5,low=9.99))
    o=SimpleNamespace(position_quantity=0,observed_at=datetime.fromtimestamp(5,timezone.utc))
    row=V.recovery_observe(state,{},market,o,0,dict(local_swings=[broken]),True)
    assert not row['local_swings']
    assert V.swing_key(broken) in state['retired_swings']


def test_stop_gain_guard_is_independent_of_position_breakout_phase():
    state={};entry=dict(confirmed_at=1,initial_fill_price=14.,
        setup=dict(phase='building',breakout_threshold=14.57))
    market=dict(body_high=14.6891,bar=dict(end=5,low=14.4,close=14.5))
    o=SimpleNamespace(position_quantity=100,observed_at=datetime.fromtimestamp(5,timezone.utc))
    V.recovery_observe(state,entry,market,o,14.39,{},False,stop_gain_guard=True)
    assert state['held']['stop_above_initial_fill']
    assert state['held']['setup']['phase']=='building'
    o.position_quantity=0
    V.recovery_observe(state,{},market,o,14.39,{},False,stop_gain_guard=True)
    market['bar']['close']=11.25
    new=swing(6,10.8469)
    assert V.recovery_permission(state,new,market)==('', 'building')
    assert V.recovery_permission(state,new,market,stop_gain_guard=True)[0]=='waiting_for_post_move_recovery_or_higher_base'
    assert V.recovery_permission(state,new,market,stop_gain_guard=True,tight_base=True)==('', 'building')
    assert V.recovery_permission(state,swing(4,10.8),market,stop_gain_guard=True,tight_base=True)[0]=='waiting_for_new_support_after_exit'
    state['last_exit']['setup']['entry_failure_recovery']=11.3
    assert V.recovery_permission(state,new,market,stop_gain_guard=True,tight_base=True)[0]=='waiting_for_failed_setup_reclaim'


def test_stop_gain_guard_never_infers_an_unobserved_initial_fill():
    state={};entry=dict(confirmed_at=1,setup=dict(phase='building',breakout_threshold=14.57))
    market=dict(body_high=14.68,bar=dict(end=5,low=14.4,close=14.5))
    o=SimpleNamespace(position_quantity=100,observed_at=datetime.fromtimestamp(5,timezone.utc))
    V.recovery_observe(state,entry,market,o,14.39,{},False,stop_gain_guard=True)
    assert not state['held']['stop_above_initial_fill']


def test_unprotected_reentry_preserves_fresh_support_and_failure_reclaim():
    import json
    state={};entry=dict(confirmed_at=1,initial_fill_price=10.2,
        setup=dict(phase='post_breakout',breakout_threshold=10.3))
    market=dict(body_high=10.5,bar=dict(end=5,low=10.,close=10.1))
    o=SimpleNamespace(position_quantity=100,observed_at=datetime.fromtimestamp(5,timezone.utc))
    V.recovery_observe(state,entry,market,o,10.,{},False,stop_gain_guard=True)
    o.position_quantity=0
    V.recovery_observe(state,{},market,o,10.,{},False,stop_gain_guard=True)
    state=json.loads(json.dumps(state))
    new=swing(6,9.9)
    strict='waiting_for_post_move_recovery_or_higher_base'
    assert V.recovery_permission(state,new,market,stop_gain_guard=True)[0]==strict
    options=dict(stop_gain_guard=True,unprotected_reentry=True)
    assert V.recovery_permission(state,new,market,**options)==('', 'building')
    assert V.recovery_permission(state,swing(4,9.9),market,**options)[0]=='waiting_for_new_support_after_exit'
    state['retired_swings'][V.swing_key(new)]=7
    assert V.recovery_permission(state,new,market,**options)[0]=='breached_setup_swing'
    state['retired_swings'].clear()
    state['last_exit']['setup']['entry_failure_recovery']=10.15
    assert V.recovery_permission(state,new,market,**options)[0]=='waiting_for_failed_setup_reclaim'
    market['bar']['close']=10.16
    assert V.recovery_permission(state,new,market,**options)==('', 'building')
    state['last_exit']['stop_above_initial_fill']=True
    assert V.recovery_permission(state,new,market,**options)[0]==strict
    state['last_exit']['stop_above_initial_fill']=False
    del state['last_exit']['initial_fill_price']
    assert V.recovery_permission(state,new,market,**options)[0]==strict


def test_unprotected_reentry_configuration_and_runtime_dispatch():
    import pytest
    from src.trading_runtime.historical_hod import configure
    host,a,obs=prepared()
    settings=a.parameters['historical_hod']
    settings.update(setup_recovery_enabled=1,setup_recovery_stop_gain_guard=1,
        setup_early_base_enabled=1,setup_base_recovery_maximum_range_pct=0,
        setup_recovery_unprotected_reentry=1)
    configure(a.parameters)
    settings=a.parameters['historical_hod']
    for invalid in (-1,2,True,float('nan'),float('inf')):
        settings['setup_recovery_unprotected_reentry']=invalid
        with pytest.raises(ValueError):configure(a.parameters)
    settings['setup_recovery_unprotected_reentry']=1
    settings['setup_recovery_stop_gain_guard']=0
    with pytest.raises(ValueError):configure(a.parameters)
    settings['setup_recovery_stop_gain_guard']=1
    o=obs(2,10.11);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[swing(now-3,9.99)]
    o=replace(o,structural_detector_state=market)
    a.state['v7_setup']['last_exit']=dict(at=now-10,entry_at=now-20,
        initial_fill_price=10.05,stop_above_initial_fill=False,
        stop=10.03,body_high=10.3,setup=dict(phase='post_breakout',breakout_threshold=10.2))
    assert any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)
    a.parameters['historical_hod']['setup_recovery_unprotected_reentry']=0
    blocked=host.evaluate(a,o)
    assert not blocked.evaluation.intents
    assert blocked.evaluation.signals[0].reason=='waiting_for_post_move_recovery_or_higher_base'


def test_entry_reclaim_does_not_exempt_a_lower_rebound_after_a_winner():
    state=dict(last_exit=dict(at=5,initial_fill_price=9.07,stop=8.89,
        stop_above_initial_fill=False,body_high=10.0066,
        setup=dict(phase='post_breakout',breakout_threshold=9.54)),retired_swings={})
    market=dict(bar=dict(close=8.51));new=swing(6,8.3)
    options=dict(stop_gain_guard=True,unprotected_reentry=True)
    assert V.recovery_permission(state,new,market,**options)==('', 'building')
    options['entry_reclaim']=True
    for close in (8.51,9.07):
        market['bar']['close']=close
        assert V.recovery_permission(state,new,market,**options)[0]=='waiting_for_post_move_recovery_or_higher_base'
    market['bar']['close']=9.08
    assert V.recovery_permission(state,new,market,**options)==('', 'building')
    state['last_exit']['stop_above_initial_fill']=True
    assert V.recovery_permission(state,new,market,**options)[0]=='waiting_for_post_move_recovery_or_higher_base'


def test_entry_reclaim_configuration_and_runtime_switch():
    import pytest
    from src.trading_runtime.historical_hod import configure
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_recovery_enabled=1,
        setup_recovery_stop_gain_guard=1,setup_recovery_unprotected_reentry=1,
        setup_recovery_entry_reclaim=1,setup_early_base_enabled=1,
        setup_base_recovery_maximum_range_pct=0)
    configure(a.parameters)
    for value in (-1,2,True,float('nan'),float('inf')):
        a.parameters['historical_hod']['setup_recovery_entry_reclaim']=value
        with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod'].update(setup_recovery_entry_reclaim=1,
        setup_recovery_unprotected_reentry=0)
    with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod']['setup_recovery_unprotected_reentry']=1
    o=obs(2,10.11);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[swing(now-3,9.99)]
    o=replace(o,structural_detector_state=market)
    previous=dict(at=now-10,entry_at=now-20,initial_fill_price=10.15,
        stop_above_initial_fill=False,stop=10.03,body_high=10.3,
        setup=dict(phase='post_breakout',breakout_threshold=10.2))
    a.state['v7_setup']['last_exit']=previous
    assert not host.evaluate(a,o).evaluation.intents
    previous['initial_fill_price']=10.05
    assert any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)


def test_regular_base_exception_keeps_history_and_support_guards():
    state=dict(last_exit=dict(at=5,stop=10.3,body_high=10.5,
        stop_above_initial_fill=True,setup=dict(phase='post_breakout',breakout_threshold=10.4)),
        retired_swings={})
    original=deepcopy(state);market=dict(bar=dict(end=15,close=10.1));new=swing(12,9.9)
    options=dict(stop_gain_guard=True,regular_session_start=10)
    assert V.recovery_permission(state,new,market,**options)==('', 'building')
    assert state==original
    assert V.recovery_permission(state,swing(8,9.9),market,**options)[0]=='waiting_for_post_move_recovery_or_higher_base'
    assert V.recovery_permission(state,swing(15,9.9),market,**options)[0]=='waiting_for_post_move_recovery_or_higher_base'
    state['retired_swings'][V.swing_key(new)]=14
    assert V.recovery_permission(state,new,market,**options)[0]=='breached_setup_swing'
    state['retired_swings'].clear()
    state['last_exit']['setup']['entry_failure_recovery']=10.2
    assert V.recovery_permission(state,new,market,**options)[0]=='waiting_for_failed_setup_reclaim'
    del state['last_exit']['setup']['entry_failure_recovery']
    state['last_exit']['at']=10
    assert V.recovery_permission(state,new,market,**options)[0]=='waiting_for_post_move_recovery_or_higher_base'


def test_regular_base_runtime_boundary_and_configuration():
    import pytest
    from zoneinfo import ZoneInfo
    from src.trading_runtime.historical_hod import configure
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_recovery_enabled=1,
        setup_recovery_stop_gain_guard=1,setup_early_base_enabled=1,
        setup_base_recovery_maximum_range_pct=0,setup_recovery_regular_base=1,
        setup_recovery_regular_full_range=1)
    configure(a.parameters)
    for value in (-1,2,True,float('nan'),float('inf')):
        a.parameters['historical_hod']['setup_recovery_regular_base']=value
        with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod'].update(setup_recovery_regular_base=1,setup_early_base_enabled=0)
    with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod']['setup_early_base_enabled']=1
    o=obs(2,10.11);now=o.observed_at.timestamp()
    boundary=o.observed_at.astimezone(ZoneInfo('America/New_York')).replace(hour=9,minute=30,second=0,microsecond=0).timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[swing(now-3,9.99)]
    o=replace(o,structural_detector_state=market)
    previous=dict(at=boundary-10,stop=10.03,body_high=10.3,stop_above_initial_fill=True,
        setup=dict(phase='post_breakout',breakout_threshold=10.2))
    a.state['v7_setup']['last_exit']=previous
    result=host.evaluate(a,o)
    assert any(i.action=='enter_long' for i in result.evaluation.intents)
    assert result.evaluation.signals[0].metadata['recovery_regular_session_start']==boundary
    previous['at']=boundary+10
    assert not host.evaluate(a,o).evaluation.intents
    previous['at']=boundary-10
    a.parameters['historical_hod'].update(setup_recovery_regular_base=0,setup_recovery_regular_full_range=0)
    assert not host.evaluate(a,o).evaluation.intents


def test_disabling_compact_base_exception_keeps_fresh_reclaim_available():
    host,a,obs=prepared()
    a.parameters['historical_hod'].update(setup_recovery_enabled=1,
        setup_recovery_stop_gain_guard=1,setup_early_base_enabled=1)
    o=obs(2,10.11);now=o.observed_at.timestamp()
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[swing(now-3,9.99)]
    o=replace(o,structural_detector_state=market)
    a.state['v7_setup']['last_exit']=dict(at=now-10,entry_at=now-20,
        stop=10.03,body_high=10.3,setup=dict(phase='post_breakout',breakout_threshold=10.2))
    assert any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)
    a.parameters['historical_hod']['setup_base_recovery_maximum_range_pct']=0
    blocked=host.evaluate(a,o)
    assert not blocked.evaluation.intents
    assert blocked.evaluation.signals[0].reason=='waiting_for_post_move_recovery_or_higher_base'
    # An actual reclaim still permits this same fresh support.
    a.state['v7_setup']['last_exit'].update(body_high=10.09)
    a.state['v7_setup']['last_exit']['setup']['breakout_threshold']=10.08
    assert any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)


def test_compact_base_limit_zero_is_valid_but_invalid_numbers_are_rejected():
    import pytest
    from src.trading_runtime.historical_hod import configure
    _,a,_=prepared()
    a.parameters['historical_hod']['setup_base_recovery_maximum_range_pct']=0
    configure(a.parameters)
    assert a.parameters['historical_hod']['setup_base_recovery_maximum_range_pct']==0
    for invalid in (-1,True,float('nan'),float('inf')):
        a.parameters['historical_hod']['setup_base_recovery_maximum_range_pct']=invalid
        with pytest.raises(ValueError):configure(a.parameters)


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


def test_regular_recovery_full_range_requires_post_open_origin():
    previous=dict(at=90,stop=10.03,body_high=10.3,stop_above_initial_fill=True,
        setup=dict(phase='post_breakout',breakout_threshold=10.2))
    market=dict(bar=dict(end=140,close=10.11))
    support=swing(130,9.99)
    for start,allowed in ((99,False),(100,True),(101,True)):
        state=dict(last_exit=previous,range=dict(start=start,end=139))
        reason,_=V.recovery_permission(state,support,market,stop_gain_guard=True,
            regular_session_start=100,regular_full_range=True)
        assert (not reason)==allowed
    for base in ({},dict(start=100,end=141),dict(start=139,end=139)):
        reason,_=V.recovery_permission(dict(last_exit=previous,range=base),support,market,
            stop_gain_guard=True,regular_session_start=100,regular_full_range=True)
        assert reason=='waiting_for_post_move_recovery_or_higher_base'
    state=dict(last_exit=previous,range=dict(start=99,end=139))
    assert not V.recovery_permission(state,support,market,stop_gain_guard=True,
        regular_session_start=100)[0]  # Prior variants preserve support-only admission.
    assert not V.recovery_permission(state,swing(130,10.04),market,stop_gain_guard=True,
        regular_session_start=100,regular_full_range=True)[0]  # Normal higher base still qualifies.


def test_regular_full_range_configuration():
    from src.trading_runtime.historical_hod import configure
    import pytest
    host,a,obs=prepared()
    p=a.parameters['historical_hod']
    p.update(setup_recovery_enabled=1,setup_early_base_enabled=1,
        setup_recovery_regular_base=1,setup_recovery_regular_full_range=1)
    configure(a.parameters)
    for value in (-1,2,True,float('nan'),float('inf')):
        a.parameters['historical_hod']['setup_recovery_regular_full_range']=value
        with pytest.raises(ValueError):configure(a.parameters)
    a.parameters['historical_hod'].update(setup_recovery_regular_full_range=1,setup_recovery_regular_base=0)
    with pytest.raises(ValueError):configure(a.parameters)
