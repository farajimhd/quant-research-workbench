import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

from src.trading_runtime import historical_hod as H, v7_encounters as E
from tests.test_v7_transition_acquisition import setup
from src.trading_runtime import strategy_engine as S


SETTINGS = dict(H.DEFAULTS, v7_encounters_enabled=1, rejection_break_offset_bps=115.)


def level(price):
    return dict(unified_level_id=str(price), price=price, lower=price-.002,
        upper=price+.002, role='transition', side=0, transition_from=None, confirmed_at_ms=1000)


def update(state, t, values, previous, levels, fresh=True, trade=False):
    bar = dict(time=t-1, end=t, **dict(zip(('open','high','low','close'),values)))
    o = SimpleNamespace(observed_at=datetime.fromtimestamp(t,timezone.utc),price=values[-1],
        evaluation_events=('bar_close',) if fresh else ('market_data_update',),
        changed_source_ids=('market.last_price',) if trade else ())
    d = dict(session='test',bar=bar,prior_bar=previous,prior_rows=levels)
    return E.update(state,o,d,SETTINGS,.01,fresh),bar


def warning():
    state={}
    prior=dict(time=2,end=3,open=4.27,high=4.28,low=4.14,close=4.22)
    _,bar=update(state,4,(4.2296,4.34,4.22,4.28),prior,[level(4.3013),level(4.3364)])
    assert E.blocked(state)
    return state,bar


def test_gray_topping_exact_half_tail_and_first_trade_exit_survive_json_restart():
    state,bar=warning()
    state=json.loads(json.dumps(state))
    assert update(state,4.01,(0,0,0,4.20),bar,[],False)[0]==''  # quote
    assert update(state,4.02,(0,0,0,4.2263),bar,[],False,True)[0]=='topping_rejection_next_open'


def test_bounce_open_cannot_be_reinterpreted_as_later_weak_open():
    state,bar=warning()
    assert not update(state,4.01,(0,0,0,4.30),bar,[],False,True)[0]
    assert not update(state,4.02,(0,0,0,4.20),bar,[],False,True)[0]
    assert E.blocked(state)


def test_recovery_requires_highest_rejected_threshold_and_frozen_geometry():
    state,bar=warning()
    before=deepcopy(state['levels'])
    _,bar=update(state,5,(4.28,4.32,4.27,4.32),bar,[level(4.1)])
    assert E.blocked(state)
    assert state['levels']['4.3364']['threshold']==before['4.3364']['threshold']
    update(state,6,(4.32,4.36,4.32,4.36),bar,[])
    assert not E.blocked(state)


def test_two_red_lower_lows_need_buffer_and_reset_after_gap():
    state,bar=warning()
    _,red=update(state,5,(4.30,4.31,4.28,4.29),bar,[])
    assert not update(state,6,(4.29,4.30,4.27,4.285),red,[])[0]
    state,bar=warning()
    _,red=update(state,5,(4.2263,4.25,4.1924,4.2),bar,[])
    assert update(state,6,(4.21,4.2272,4.14,4.1503),red,[])[0]=='buffered_resistance_failure'
    state,bar=warning()
    assert not update(state,8,(4.21,4.22,4.14,4.15),bar,[])[0]


def test_engine_buffer_and_rejection_veto_and_swing_stop():
    host,a,obs=setup()
    parameters=deepcopy(a.parameters)
    parameters['historical_hod'].update(v7_encounters_enabled=1,v7_price_only_enabled=1)
    a=replace(a,parameters=parameters)
    assert not any(i.action=='enter_long' for i in host.evaluate(a,obs(2,10.02)).evaluation.intents)
    entered=host.evaluate(a,obs(2,10.04))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents)
    state=deepcopy(a.state)
    state['v7_encounters']=dict(session=state['historical_hod_state']['session'],levels={
        'overhead':dict(level=level(11),threshold=11.02,failure_threshold=10.8,status='failed')})
    blocked=host.evaluate(replace(a,state=state),obs(2,10.04))
    assert blocked.evaluation.signals[0].reason=='unresolved_level_rejection'


def test_engine_never_uses_legacy_resistance_base_as_a_swing_exit():
    host,a,obs=setup()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_encounters_enabled=1,v7_price_only_enabled=1)
    a=replace(a,parameters=p)
    entered=host.evaluate(a,obs(2,10.04))
    state=entered.state
    # Deliberately adverse old resistance base, but a valid broker swing stop.
    state['historical_hod_entry']['management_base']={'lower':10.2,'tolerance':.01}
    state['historical_hod_entry']['failure_closes']=2
    a=replace(a,state=state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(obs(3,10.03),bar_open=10.04,position_quantity=100,average_price=10.04))
    assert not any(i.action=='exit' for i in r.evaluation.intents)
    r=host.evaluate(replace(a,state=r.state),replace(obs(4,9.97),position_quantity=100,average_price=10.04))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in r.evaluation.intents)


def test_engine_executes_next_open_failure_before_macd_and_acquisition_gates():
    host,a,obs=setup()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_encounters_enabled=1,v7_price_only_enabled=1)
    a=replace(a,parameters=p)
    entered=host.evaluate(a,obs(2,10.04))
    state=entered.state
    end=obs(3,10.04).observed_at.timestamp()
    state['v7_encounters']=dict(session=state['historical_hod_state']['session'],levels={
        'overhead':dict(level=level(10.3),threshold=10.31,failure_threshold=10.1,status='warning',
            warning=dict(open=10.05,end=end))})
    a=replace(a,state=state,status=S.AssignmentStatus.MANAGING)
    o=replace(obs(3,10.04),evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',),
        position_quantity=100,average_price=10.04,bid=0,ask=0)
    r=host.evaluate(a,o)
    assert any(i.action=='exit' and i.reason=='topping_rejection_next_open' for i in r.evaluation.intents)


def test_red_add_break_must_still_hold_buffer_on_green_confirmation():
    host,a,obs=setup()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_encounters_enabled=1,v7_price_only_enabled=1)
    a=replace(a,parameters=p)
    entered=host.evaluate(a,obs(2,10.04))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    red=replace(obs(3,10.19),bar_open=10.20,bar_high=10.21,bar_low=10.18,
        position_quantity=100,average_price=10.04)
    r=host.evaluate(a,red)
    assert not any(i.action=='add_long' for i in r.evaluation.intents)
    green=replace(obs(4,10.165),bar_open=10.16,bar_high=10.165,bar_low=10.155,
        position_quantity=100,average_price=10.04)
    r=host.evaluate(replace(a,state=r.state),green)
    assert not any(i.action=='add_long' for i in r.evaluation.intents)
