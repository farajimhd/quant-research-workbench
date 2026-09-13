"""Exercise gray-level acquisition through the runnable strategy engine."""
from copy import deepcopy
from dataclasses import replace
import pytest
from src.trading_runtime import historical_hod as H, strategy_engine as S
from tests.test_structural_recovery import NOW
from tests.test_v7_center_swing_strategy import candidate


def setup(origin='support', enabled=True):
    host,a,obs=candidate(True)
    p=deepcopy(a.parameters)
    p['historical_hod'].update(v7_transition_entries_enabled=int(enabled),
        forming_macd_entry_enabled=1,sizing_mode='cash_tranches')
    state=deepcopy(a.state)
    d=state['historical_hod_state']
    d.update(episode=None,macd_positive=False,completed_macd=dict(
        at=NOW.timestamp(),slow=10.,line=-.001,signal=0.))
    for r in d['rows']:
        if r.get('role')=='transition':r['transition_from']=origin
    a=replace(a,parameters=p,state=state,permissions=S.StrategyPermissions(enter=True,reenter=True,add=True))
    def observation(i,price):
        o=obs(i,price)
        return replace(o,structural_transition_levels=tuple(
            dict(r,transition_from=origin) for r in o.structural_transition_levels))
    return host,a,observation


@pytest.mark.parametrize('origin',['support','resistance'])
def test_gray_center_entry_with_bullish_preview_while_completed_macd_is_bearish(origin):
    host,a,obs=setup(origin)
    result=host.evaluate(a,obs(2,10.02))
    assert any(i.action=='enter_long' for i in result.evaluation.intents)
    m=result.evaluation.signals[0].metadata
    assert m['macd']['kind']=='forming' and m['macd']['line']>m['macd']['signal']
    assert result.state['historical_hod_state']['completed_macd']['line']<0
    assert m['entry_breakout_confirmation']['threshold']==pytest.approx(10.01)
    assert result.state['historical_hod_entry']['level']['transition_from']==origin


def test_previous_candidate_still_excludes_support_origin_transition():
    host,a,obs=setup(enabled=False)
    assert not host.evaluate(a,obs(2,10.02)).evaluation.intents


@pytest.mark.parametrize('failure',['red','intrabar','bearish','stale','spread'])
def test_gray_level_does_not_bypass_acquisition_gates(failure):
    host,a,obs=setup()
    o=obs(2,10.02)
    if failure=='red':o=replace(o,bar_open=10.03)
    if failure=='intrabar':o=replace(o,evaluation_events=('market_data_update',))
    if failure=='bearish':a.state['historical_hod_state']['completed_macd']['signal']=.1
    if failure=='stale':a.state['historical_hod_state']['completed_macd']['at']-=5
    if failure=='spread':o=replace(o,bid=9.9,ask=10.1)
    assert not host.evaluate(a,o).evaluation.intents


@pytest.mark.parametrize('origin',['support','resistance'])
@pytest.mark.parametrize('red',[False,True])
def test_add_on_gray_center_before_upper_band_without_changing_stop(origin,red):
    host,a,obs=setup(origin)
    entered=host.evaluate(a,obs(2,10.02))
    a=replace(a,state=entered.state,status=S.AssignmentStatus.MANAGING)
    def positioned(i,price):
        o=obs(i,price)
        levels=list(o.structural_resistance_levels)
        gray=levels.pop(0)  # center 10.16, upper 10.17
        return replace(o,position_quantity=100,average_price=10.025,
            structural_resistance_levels=tuple(levels),
            structural_transition_levels=(*o.structural_transition_levels,
                dict(gray,side=0,role='transition',transition_from=origin)))
    approach=host.evaluate(a,positioned(3,10.10))
    a=replace(a,state=approach.state,status=S.AssignmentStatus.MANAGING)
    o=positioned(4,10.165)
    if red:o=replace(o,bar_open=10.17)
    result=host.evaluate(a,o)
    adds=[i for i in result.evaluation.intents if i.action=='add_long']
    assert bool(adds) is not red
    assert not any(i.action=='replace_protective_stop' for i in result.evaluation.intents)
    if not red:
        signal=next(s for s in result.evaluation.signals if s.action=='add_long')
        assert signal.metadata['tranche_breakout_confirmation']['threshold']==pytest.approx(10.16)
        assert result.state['historical_hod_entry']['tranches_requested']==2


def test_acquisition_does_not_reclassify_gray_as_rejection_resistance():
    _,a,obs=setup()
    gray=next(r for r in H.selected_levels(obs(2,10.02),a.parameters['historical_hod'],NOW.timestamp()+2)
        if r['role']=='transition')
    assert H.acquisition_level(gray,a.parameters['historical_hod'])
    assert not H.resistance(gray)


@pytest.mark.parametrize('close,expected',[(9.995,False),(9.99,False),(9.989,True)])
def test_rejection_requires_offset_below_frozen_lower_band(close,expected):
    at=NOW.timestamp()
    previous=dict(time=at+2,end=at+3,open=10.02,high=10.03,low=10.0,close=10.01)
    bar=dict(time=at+3,end=at+4,open=10.01,high=10.02,low=close-.001,close=close)
    level=dict(side='resistance',lower=10.,upper=10.04,confirmed_at=at+1,
        level_id='band',oldest_member_confirmed_at_ms=(at-86400)*1000)
    active=dict(confirmed_at=at+1,management_base=dict(lower=9.,tolerance=.01))
    s=dict(H.DEFAULTS,rejection_break_offset_bps=10.)
    reason=H.management({},active,bar,s,.01,previous_bar=previous,resistance_levels=[level])
    assert (reason=='red_close_below_attempt_open')==expected
    if expected:
        assert active['failed_resistance_exit']['failure_threshold']==pytest.approx(9.99)


def test_pending_red_confirmation_cannot_ignore_offset():
    from tests.test_historical_hod import candle
    at=NOW.timestamp()
    level=dict(side='resistance',lower=10.,upper=10.04,
        oldest_member_confirmed_at_ms=(at-86400)*1000)
    active=dict(pending_failed_attempt=dict(at_ms=round((at+3)*1000),trigger_close=9.989),
        failed_resistance_exit=dict(level=level,failure_threshold=9.99,offset_bps=10.))
    previous=dict(end=at+3,open=10.01,low=9.985)
    for close,expected in [(9.995,False),(9.989,True)]:
        o=replace(candle(4,close,opened=10.0),bar_low=9.98)
        assert H.confirm_failed_attempt(deepcopy(active),o,previous_bar=previous)==expected


def test_candidate_publishes_forming_macd_gray_centers_and_configurable_offset():
    from src.backend import v7_zone_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    payload,canvas,plan=C.build(configuration_base())
    validated,_,_=_build_configuration_release(configuration=payload,canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'],run_plan_id=plan,strategy_profile_id=C.PROFILE_ID)
    profile=next(p for p in validated['strategy']['profiles'] if p['profile_id']==C.PROFILE_ID)
    s=profile['parameters']['historical_hod']
    assert s['forming_macd_entry_enabled']==1
    assert s['v7_transition_entries_enabled']==1
    assert s['rejection_break_offset_bps']==115


@pytest.mark.parametrize('close,expected',[(3.95,False),(3.93,False),(3.929,True)])
def test_115_bps_rejection_buffer_holds_until_below_saved_band_threshold(close,expected):
    at=NOW.timestamp()
    previous=dict(time=at+2,end=at+3,open=4.,high=4.,low=3.96,close=3.98)
    bar=dict(time=at+3,end=at+4,open=3.99,high=4.,low=close-.01,close=close)
    level=dict(side='resistance',lower=3.97569858298652,upper=4.016966832064307,
        confirmed_at=at+1,level_id='saved-band',oldest_member_confirmed_at_ms=(at-86400)*1000)
    active=dict(confirmed_at=at+1,management_base=dict(lower=3.5,tolerance=.01))
    settings=dict(H.DEFAULTS,rejection_break_offset_bps=115.)
    reason=H.management({},active,bar,settings,.01,previous_bar=previous,resistance_levels=[level])
    assert (reason=='red_close_below_attempt_open')==expected
    if expected:
        assert active['failed_resistance_exit']['failure_threshold']==pytest.approx(3.9299780492821754)
