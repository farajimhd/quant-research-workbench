from copy import deepcopy
from dataclasses import replace
import pytest
from src.trading_runtime import historical_hod as H, strategy_engine as S
from tests.test_historical_hod import candle,ready
from tests.test_structural_recovery import NOW,level

VERSION='causal-level-book-v7-mle-1'


def band(side,low,high,old=False,**kw):
    return dict(level(side,low,high),book_version=VERSION,lifecycle='active',
        role='resistance' if side==-1 else 'support' if side==1 else 'transition',
        oldest_member_confirmed_at_ms=(NOW.timestamp()-(86400 if old else 10))*1000,
        fit={'status':'estimated'},**kw)


def setup(transition=False,old=False):
    host,a,_=ready()
    p=deepcopy(a.parameters);p['historical_hod'].update(v7_zone_enabled=1,entry_zone_fraction=.3,entry_breakout_offset=.01)
    a=replace(a,parameters=p,state={})
    levels=(band(-1,10.,10.02,old),band(-1,10.15,10.17,old),band(-1,10.9,10.92,old),band(-1,11.5,11.52,old))
    def obs(i,close):
        o=candle(i,close,source_timeframe='5s' if i==0 else '1s')
        market=deepcopy(o.structural_detector_state);market['book']['version']=VERSION
        transition_level=dict(levels[0],side=0,role='transition',transition_from='resistance')
        return replace(o,structural_detector_state=market,execution_vwap=9.8,structural_session_high=10.1,
            structural_resistance_levels=levels[1:] if transition else levels,
            structural_transition_levels=(transition_level,) if transition else (),
            structural_support_levels=(band(1,10.,10.005,old),))
    for i,close in ((0,10.),(1,10.01)):
        r=host.evaluate(a,obs(i,close));a=replace(a,state=r.state,status=r.status)
    return host,a,obs


@pytest.mark.parametrize('transition',[False,True])
@pytest.mark.parametrize('old',[False,True])
def test_new_entry_and_closer_support_stop_for_both_origins(transition,old):
    host,a,obs=setup(transition,old)
    r=host.evaluate(a,obs(2,10.04))
    assert r.evaluation.signals[0].action=='enter_long',r.evaluation.signals[0].reason
    assert r.state['initial_stop']==pytest.approx(9.99)
    entry=r.state['historical_hod_entry']
    assert entry['level']['role']==('transition' if transition else 'resistance')
    ref=r.evaluation.signals[0].metadata['historical_hod_reference']
    assert ref['hod']==10.1 and ref['zone_lower']==pytest.approx(10.01)


def test_zone_has_no_hod_fallback_and_rejects_support_transition():
    host,a,obs=setup(True)
    for rows in ([],[dict(a.state['historical_hod_state']['rows'][0],side=0,role='transition',transition_from='support')]):
        state=deepcopy(a.state);state['historical_hod_state']['rows']=rows
        r=host.evaluate(replace(a,state=state),obs(2,10.04))
        assert not r.evaluation.intents


def test_zone_and_red_candle_gates():
    host,a,obs=setup()
    for o in (replace(obs(2,10.04),bar_open=10.05),replace(obs(2,10.04),execution_vwap=10.035)):
        assert not host.evaluate(a,o).evaluation.intents


def test_zone_selection_uses_closest_upcoming_resistance_not_hod_neighbor():
    host,a,obs=setup()
    d=deepcopy(a.state['historical_hod_state']);d.update(prior_rows=d['rows'],prior_close=10.01,hod=10.1,vwap=9.8)
    d['prior_rows'].append(dict(d['rows'][0],lower=10.06,price=10.07,upper=10.08))
    assert H.zone_reference(d,obs(2,10.04),a.parameters['historical_hod'])['level']['upper']==10.02


def test_v7_target_does_not_distinguish_origins():
    host,a,obs=setup()
    s=a.parameters['historical_hod'];s['target_distance_fraction']=.1
    current=H.selected_levels(obs(2,10.04),s,NOW.timestamp()+2)
    old=[dict(r,oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000) for r in current]
    targets=[H.available_target(rows,10.04,s,.01,session='2026-08-21') for rows in (current,old)]
    assert targets[0]['price']==targets[1]['price']


@pytest.mark.parametrize('transition',[False,True])
def test_add_and_stop_advance_on_current_day_resistance_or_transition(transition):
    host,a,obs=setup()
    p=deepcopy(a.parameters);p['historical_hod'].update(sizing_mode='cash_tranches')
    a=replace(a,parameters=p,permissions=S.StrategyPermissions(enter=True,reenter=True,add=True))
    entered=host.evaluate(a,obs(2,10.04))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    # Price approaches the next level before clearing it, including when it
    # has already entered a resistance-origin transition in V7.
    for i,price in ((3,10.10),(4,10.20),(5,10.21)):
        o=obs(i,price)
        if transition:
            rs=list(o.structural_resistance_levels);r=rs.pop(1)
            o=replace(o,structural_resistance_levels=tuple(rs),structural_transition_levels=(dict(r,side=0,role='transition',transition_from='resistance'),))
        o=replace(o,position_quantity=100,average_price=10.045)
        result=host.evaluate(a,o)
        if i==4:
            assert any(x.action=='add_long' for x in result.evaluation.intents)
            assert not any(x.action=='replace_protective_stop' for x in result.evaluation.intents)
        if i==5:
            stops=[x for x in result.evaluation.intents if x.action=='replace_protective_stop']
            assert len(stops)==1 and stops[0].invalidation_price==pytest.approx(10.14)
        a=replace(a,state=result.state,status=S.AssignmentStatus.MANAGING)
