from copy import deepcopy
from dataclasses import replace
from src.trading_runtime import v7_setup as V, strategy_engine as S
from tests.test_v7_transition_acquisition import setup


def test_range_is_prior_only_and_phase_survives_episode_change():
    state={}
    settings=dict(setup_range_seconds=30,setup_minimum_bars=5)
    for i in range(1,7):
        d=dict(session='day',episode=1,bar=dict(time=i-1,end=i,open=10,close=10.1,high=10.2 if i<6 else 11,low=9.9))
        V.observe(state,d,settings,True)
    assert state['range']['high']==10.2
    entry=dict(confirmed_at=6,setup=dict(phase='building',breakout_threshold=10.21,range=deepcopy(state['range'])))
    d.update(episode=2,bar=dict(time=6,end=7,open=10.3,close=10.25,high=10.4,low=10))
    assert not V.phase(entry,d,True)  # red cannot activate
    d['bar'].update(end=8,time=7,open=10.2,close=10.25)
    assert V.phase(entry,d,True)
    assert entry['setup']['range']['high']==10.2
    assert not V.phase(entry,d,True)


def prepared():
    host,a,obs=setup()
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_setup_enabled=1,v7_encounters_enabled=1,v7_price_only_enabled=1)
    state=deepcopy(a.state)
    end=obs(2,10.02).observed_at.timestamp()
    state['v7_setup']=dict(session=state['historical_hod_state']['session'],bars=[
        dict(time=end-i-1,end=end-i,open=10,close=10,high=10.1,low=9.99) for i in range(6,0,-1)])
    return host,replace(a,parameters=p,state=state),obs


def test_early_entry_before_resistance_and_hold_rejection_then_stop():
    host,a,obs=prepared()
    # Price above previous close but below next resistance 10.16 and range 10.1.
    entered=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in entered.evaluation.intents),entered.evaluation.signals[0].reason
    active=entered.state['historical_hod_entry']
    assert active['setup']['phase']=='building'
    assert active['level']['price']>10.02
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    end=obs(3,10.01).observed_at.timestamp()
    a.state['v7_encounters']=dict(session=a.state['historical_hod_state']['session'],levels={
        'x':dict(level=dict(price=10.2,lower=10.1,upper=10.3),threshold=10.21,failure_threshold=10.1,
            status='warning',warning=dict(open=10.1,end=end))})
    o=replace(obs(3,10.01),position_quantity=100,average_price=10.02,
        evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',))
    result=host.evaluate(a,o)
    assert not any(i.action=='exit' for i in result.evaluation.intents)
    result=host.evaluate(replace(a,state=result.state),replace(o,price=9.97))
    assert any(i.action=='exit' and i.reason=='protective_stop' for i in result.evaluation.intents)


def test_early_entry_still_rejects_red_and_wide_spread():
    host,a,obs=prepared()
    for o in [replace(obs(2,10.02),bar_open=10.03),replace(obs(2,10.02),bid=9.9,ask=10.1)]:
        assert not any(i.action=='enter_long' for i in host.evaluate(a,o).evaluation.intents)


def test_post_breakout_enables_only_new_rejections():
    host,a,obs=prepared()
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(obs(3,10.13),position_quantity=100,average_price=10.02))
    assert r.state['historical_hod_entry']['setup']['phase']=='post_breakout'
    assert not r.state['v7_encounters']
    end=obs(4,10.12).observed_at.timestamp()
    r.state['v7_encounters']=dict(session=r.state['historical_hod_state']['session'],levels={
        'x':dict(level=dict(price=10.3,lower=10.2,upper=10.4),threshold=10.31,failure_threshold=10.15,
            status='warning',warning=dict(open=10.2,end=end))})
    o=replace(obs(4,10.12),position_quantity=100,average_price=10.02,
        evaluation_events=('market_data_update',),changed_source_ids=('market.last_price',))
    r=host.evaluate(replace(a,state=r.state),o)
    assert any(i.action=='exit' and i.reason=='topping_rejection_next_open' for i in r.evaluation.intents)
