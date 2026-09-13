from copy import deepcopy
from dataclasses import replace
import pytest
from src.trading_runtime import strategy_engine as S
from tests.test_v7_zone_strategy import setup


def candidate(transition=False):
    host,a,obs=setup(transition)
    p=deepcopy(a.parameters)
    p['historical_hod']['v7_center_swing_enabled']=1
    return host,replace(a,parameters=p),obs


def test_price_only_entry_ignores_quote_and_swing_band_overlap_but_keeps_spread_gate():
    host,a,obs=candidate()
    p=deepcopy(a.parameters);p['historical_hod']['v7_price_only_enabled']=1
    a=replace(a,parameters=p)
    o=obs(2,10.02)
    market=deepcopy(o.structural_detector_state)
    # The confirmed swing price and resulting stop are below price, even
    # though its uncertainty band overlaps price and bid lies below the zone.
    market['row']['local_swings']=[dict(side='support',state='active',lower=9.99,
        price=10.,upper=10.04,pivot_at=o.observed_at.timestamp()-2,
        confirmed_at=o.observed_at.timestamp()-1)]
    results=[]
    for bid,ask in [(9.99,10.03),(10.01,10.04)]:
        r=host.evaluate(a,replace(o,bid=bid,ask=ask,structural_detector_state=market))
        assert r.evaluation.signals[0].action=='enter_long',r.evaluation.signals[0].reason
        results.append(r.state['historical_hod_entry'])
        assert r.state['initial_stop']==pytest.approx(9.98)
        assert r.state['entry_reference_price']==10.02
    assert results[0]['target']==results[1]['target']
    # Execution may price its buy envelope from the ask without changing
    # the strategy's entry, stop or target decisions.
    assert results[0]['maximum_buy_price']>=10.03
    assert results[1]['maximum_buy_price']>=10.04
    r=host.evaluate(a,replace(o,bid=9.9,ask=10.1,structural_detector_state=market))
    assert not r.evaluation.intents
    assert 'current_spread' in r.evaluation.signals[0].metadata['liquidity_admission']['failed']


@pytest.mark.parametrize('transition',[False,True])
def test_center_close_opens_gate_before_upper_band_and_support_never_sets_stop(transition):
    host,a,obs=candidate(transition)
    o=obs(2,10.02)  # Center 10.01 is cleared; upper+offset 10.03 is not.
    r=host.evaluate(a,o)
    assert r.evaluation.signals[0].action=='enter_long',r.evaluation.signals[0].reason
    assert r.state['initial_stop']==pytest.approx(9.98)
    assert r.evaluation.signals[0].metadata['entry_breakout_confirmation']['threshold']==pytest.approx(10.01)
    assert r.evaluation.signals[0].metadata['historical_hod_reference']['resistance_center']==pytest.approx(10.01)
    old=deepcopy(a.parameters);old['historical_hod']['v7_center_swing_enabled']=0
    assert not host.evaluate(replace(a,parameters=old),o).evaluation.intents


def test_center_requires_completed_non_red_candle_and_valid_confirmed_swing():
    host,a,obs=candidate()
    o=obs(2,10.02)
    assert not host.evaluate(a,replace(o,bar_open=10.03)).evaluation.intents
    assert not host.evaluate(a,replace(o,evaluation_events=('market_data_update',))).evaluation.intents
    for invalid in ('missing','future'):
        market=deepcopy(o.structural_detector_state)
        if invalid=='missing':market['row'].update(local_swings=[],confirmed_swings=[])
        else:market['row']['local_swings'][0]['confirmed_at']=o.observed_at.timestamp()+1
        r=host.evaluate(a,replace(o,structural_detector_state=market))
        assert not r.evaluation.intents
        assert r.evaluation.signals[0].reason=='confirmed_local_swing_low_unavailable'


@pytest.mark.parametrize('price_only',[False,True])
def test_only_new_confirmed_swing_lows_raise_stop_and_never_loosen(price_only):
    host,a,obs=candidate()
    p=deepcopy(a.parameters);p['historical_hod']['v7_price_only_enabled']=int(price_only)
    a=replace(a,parameters=p)
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    for i,price in [(3,10.10),(4,10.20),(5,10.21)]:
        r=host.evaluate(a,replace(obs(i,price),position_quantity=100,average_price=10.025))
        assert not any(x.action=='replace_protective_stop' for x in r.evaluation.intents)
        a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    for i,low in [(6,10.10),(7,10.05),(8,10.15)]:
        o=replace(obs(i,10.25),position_quantity=100,average_price=10.025)
        if price_only:o=replace(o,bid=10.04,ask=10.08)
        market=deepcopy(o.structural_detector_state);now=o.observed_at.timestamp()
        swing=dict(side='support',state='active',lower=low,price=low+.002,upper=low+.005,pivot_at=now-1,confirmed_at=now)
        # A higher future-confirmed swing must not influence this stop.
        market['row'].update(local_swings=[swing,dict(swing,lower=10.20,price=10.202,upper=10.205,confirmed_at=now+1)])
        r=host.evaluate(a,replace(o,structural_detector_state=market))
        stops=[x for x in r.evaluation.intents if x.action=='replace_protective_stop']
        if i==7:assert not stops and r.state['active_stop']==pytest.approx(10.09)
        else:
            assert len(stops)==1 and stops[0].invalidation_price==pytest.approx(low-.01)
            assert stops[0].reason=='confirmed_swing_low_trail'
        a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
