from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import historical_hod as H, strategy_engine as S
from tests.test_structural_recovery import parameters, observation, level, NOW


def rows():
    return tuple(dict(level(-1,x,x+.02),oldest_member_confirmed_at_ms=
        (NOW.timestamp()-86400 if x in (10.,10.4) else NOW.timestamp()-10)*1000)
        for x in (10.,10.4,10.55,10.95,11.5))


def candle(i,price,opened=None,**kw):
    o,_ = observation(i,price-.01 if opened is None else opened,price)
    market=deepcopy(o.structural_detector_state)
    market['row']['local_swings']=[dict(side='support',lower=9.99,price=9.992,upper=9.995,
        pivot_at=NOW.timestamp()-10,confirmed_at=NOW.timestamp()-5)]
    return replace(o,bar_volume=1000,execution_vwap=9.8,macd_line=.01,macd_signal=0.,
        structural_session_high=10.3,structural_support_levels=(),structural_resistance_levels=rows(),
        structural_detector_state=market,**kw)


def ready():
    p=parameters()
    p.pop('structural_recovery_contract');p.pop('structural_recovery')
    p.update(historical_hod_contract=H.CONTRACT,historical_hod=dict(H.DEFAULTS))
    p=S.resolve_long_momentum_parameters(p,revision=47)
    a=S.StrategyAssignment('historical',S.STRATEGY_ID,47,'sim','TEST',123,S.AssignmentStatus.WATCHING,
        S.StrategyPermissions(enter=True,reenter=True),p)
    host=S.LongMomentumStrategyEngine(revision=47)
    for o in (candle(0,10.,source_timeframe='5s'),candle(1,10.01)):
        r=host.evaluate(a,o);a=replace(a,state=r.state,status=r.status)
    return host,a,candle(2,10.04)


def acquired():
    host,a,o=ready();r=host.evaluate(a,o)
    assert r.evaluation.signals[0].action=='enter_long'
    return host,replace(a,state=r.state,status=S.AssignmentStatus.MANAGING),o


def test_entry_close_non_red_and_complete_broker_protection():
    host,a,o=ready();r=host.evaluate(a,o)
    intent,=r.evaluation.intents
    assert intent.action=='enter_long'
    assert intent.invalidation_price==pytest.approx(9.98)
    assert intent.profit_target_price==pytest.approx(10.54)
    assert len(intent.protection_profile.slices)==1
    assert intent.metadata['mandatory_broker_target']
    assert intent.resolved_execution_policy().envelope.deadline_ms==1000
    assert S.strategy_rule_timeframes(a.parameters)=={'100ms','1s','5s'}
    assert host.evaluate(a,replace(o,bar_open=10.06)).evaluation.signals[0].reason=='red_breakout_candle'
    assert host.evaluate(a,replace(o,bar_open=o.price)).evaluation.signals[0].action=='enter_long'
    assert not host.evaluate(a,replace(o,evaluation_events=('market_data_update',))).evaluation.intents
    assert not host.evaluate(a,replace(o,price=10.02,bar_high=10.1)).evaluation.intents


def test_historical_priority_merged_ancestry_and_fallbacks():
    r=list(rows());session='2026-08-21'
    r.append(dict(level(-1,10.2,10.22),oldest_member_confirmed_at_ms=NOW.timestamp()*1000))
    assert H.entry_level(r,10.3,session)['lower']==10.
    r[0]['confirmed_at_ms']=NOW.timestamp()*1000
    assert H.historical(r[0],session)
    assert H.entry_level(r[1:],10.3,session)['lower']==10.2
    assert H.entry_level([],10.3,session)['reference_kind']=='hod'


def test_initial_stop_uses_latest_confirmed_local_low_and_waits_when_missing():
    host,a,o=ready()
    old=dict(side='support',lower=9.9,price=9.91,upper=9.92,pivot_at=NOW.timestamp()-8,confirmed_at=NOW.timestamp()-4)
    latest=dict(old,lower=9.8,price=9.81,upper=9.82,pivot_at=NOW.timestamp()-3,confirmed_at=o.observed_at.timestamp())
    row=o.structural_detector_state['row']
    row.update(local_swings=[old],confirmed_swings=[latest])
    r=host.evaluate(a,o)
    assert r.evaluation.intents[0].invalidation_price==pytest.approx(9.79)
    assert r.evaluation.intents[0].metadata['initial_stop_selection']==latest
    # Management still starts at the broken resistance; this is an initial-stop change only.
    assert r.state['historical_hod_entry']['management_base']['lower']==10.
    row.update(local_swings=[],confirmed_swings=[])
    assert host.evaluate(a,o).evaluation.signals[0].reason=='confirmed_local_swing_low_unavailable'
    row['confirmed_swings']=[dict(latest,confirmed_at=o.observed_at.timestamp()+1),
        dict(latest,lower=10.1,price=10.11,upper=10.12),dict(latest,side='resistance')]
    assert host.evaluate(a,o).evaluation.signals[0].reason=='confirmed_local_swing_low_unavailable'


def test_fresh_cross_vwap_liquidity_and_stale_macd():
    host,a,o=ready()
    for changed in (replace(o,execution_vwap=o.price),replace(o,source_values={}),
                    candle(6,10.04),replace(o,bar_volume=0)):
        assert not host.evaluate(a,changed).evaluation.intents
    state=deepcopy(a.state);state['historical_hod_state']['close']=10.03
    assert not host.evaluate(replace(a,state=state),o).evaluation.intents
    no_target=deepcopy(a.state);no_target['historical_hod_state']['rows']=[rows()[0]]
    assert host.evaluate(replace(a,state=no_target),o).evaluation.signals[0].reason=='qualified_target_unavailable'


def test_sugp_entry_accepts_fresh_ask_above_old_close_based_cap():
    host,a,_=ready()
    levels=tuple(dict(level(-1,low,high),price=price,
        oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
        for low,price,high in ((4.2736451,4.2745,4.290858),(4.4061186,4.44,4.440888)))
    a.state['historical_hod_state'].update(close=4.26,rows=levels,hod=4.34)
    o=replace(candle(2,4.3003,opened=4.27),bid=4.30,ask=4.31,execution_vwap=4.,
        structural_session_high=4.34,structural_resistance_levels=levels)
    o.structural_detector_state['row']['local_swings']=[dict(side='support',lower=4.25,price=4.255,
        upper=4.26,pivot_at=NOW.timestamp()-10,confirmed_at=NOW.timestamp()-5)]
    r=host.evaluate(a,o)
    intent,=r.evaluation.intents
    assert intent.action=='enter_long'
    assert intent.metadata['maximum_buy_price']==pytest.approx(4.31*1.0015)
    assert intent.profit_target_price==pytest.approx(4.46)
    assert intent.metadata['profit_target_selection']['placement']=='above_upper_band'
    assert not host.evaluate(a,replace(o,ask=4.40)).evaluation.intents


def test_target_placement_switches_at_five_percent_reference():
    broken={'price':10.,'upper':10.01}
    near=dict(level(-1,10.39,10.413),price=10.4)
    far=dict(level(-1,10.59,10.61),price=10.6)
    selected=H.target_selection([near],broken,10.1,H.DEFAULTS,.01)
    assert selected['price']==pytest.approx(10.43)
    assert selected['placement']=='above_upper_band'
    selected=H.target_selection([far],broken,10.1,H.DEFAULTS,.01)
    assert selected['price']==pytest.approx(10.58)
    assert selected['placement']=='below_lower_band'
    # Eligibility must use the actual upper-band placement, not the old lower-band price.
    assert H.target_selection([near],broken,10.40,H.DEFAULTS,.01)['price']==pytest.approx(10.43)


def test_only_completed_5s_macd_ends_episode():
    host,a,_=acquired()
    bearish=replace(candle(3,10.1,position_quantity=100),macd_line=-.1)
    assert host.evaluate(a,bearish).evaluation.signals[0].action=='hold'
    for change in ({'source_timeframe':'5s','evaluation_events':('market_data_update',)},):
        assert host.evaluate(a,replace(bearish,**change)).evaluation.signals[0].action=='hold'
    r=host.evaluate(a,replace(bearish,source_timeframe='5s'))
    assert r.evaluation.intents[0].reason=='macd_episode_ended'
    assert r.evaluation.intents[0].metadata['reentry_after_fill']


def test_spread_blocked_breakout_failure_requires_episode_body_high_without_fill():
    host,a,_=ready()
    for i,price in ((2,10.2),(3,10.01)):
        o=replace(candle(i,price),ask=price+.2,bid=price-.2)
        r=host.evaluate(a,o)
        assert r.evaluation.signals[0].reason=='tradability_incomplete'
        a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.04))
    assert r.evaluation.signals[0].reason=='waiting_for_fresh_body_high_break'
    assert r.evaluation.signals[0].metadata['entry_selection']['failed_breakout']
    assert not r.state['historical_hod_state']['used_episode']
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(5,10.21))
    assert r.evaluation.signals[0].action=='enter_long'


def test_failed_breakout_is_observed_passively_and_resets_next_macd_episode():
    host,a,_=ready();saved={}
    for i,price,tf in ((0,10.,'5s'),(1,10.01,'1s'),(2,10.2,'1s'),
                       (3,10.01,'1s'),(4,10.04,'1s')):
        obs=candle(i,price,source_timeframe=tf)
        frame=SimpleNamespace(as_of=obs.observed_at,timeframe=tf,bar=dict(open=obs.bar_open,
            high=obs.bar_high,low=obs.bar_low,close=obs.price,volume=1000),
            indicator=dict(macd_line=.01,macd_signal=0,execution_vwap=9.8))
        saved=H.observe_frame(frame,saved,a.parameters,{'unified_levels':rows(),'session_high':10.3})
    market=deepcopy(obs.structural_detector_state);market['historical_hod_observation']=saved
    r=host.evaluate(replace(a,state={}),replace(obs,structural_detector_state=market))
    assert r.evaluation.signals[0].reason=='waiting_for_fresh_body_high_break'
    d=deepcopy(r.state['historical_hod_state'])
    H.observe(replace(candle(5,10.01,source_timeframe='5s'),macd_line=-.01),d,H.DEFAULTS)
    H.observe(candle(10,10.01,source_timeframe='5s'),d,H.DEFAULTS)
    assert not d['failed_breakout'] and d['breakout_upper'] is None


def test_target_advances_on_break_while_stop_waits_one_more_close():
    host,a,_=acquired()
    for i,price in ((3,10.43),(4,10.44)):
        r=host.evaluate(a,candle(i,price,position_quantity=100))
        if i==3:
            assert r.state['active_stop']==pytest.approx(9.98)
        if i==3:
            intent,=r.evaluation.intents
            assert intent.action=='replace_profit_target'
            assert intent.profit_target_price==pytest.approx(10.98)
            assert intent.metadata['profit_target_selection']['reference']==pytest.approx(10.56*1.05)
        a=replace(a,state=r.state,status=r.status)
    assert r.evaluation.intents[0].action=='replace_protective_stop'
    assert r.state['active_stop']==pytest.approx(10.39)
    assert r.evaluation.intents[0].metadata['previous_stop']==pytest.approx(9.98)
    assert r.state['structural_profit_targets']==pytest.approx([10.98])
    assert r.state['historical_hod_entry']['last_cleared_resistance']['upper']==10.42


def test_entry_resistance_is_counted_and_recrossing_it_does_not_move_initial_stop():
    host,a,_=acquired()
    assert a.state['historical_hod_entry']['last_cleared_resistance']==a.state['historical_hod_entry']['level']
    for i,price in ((3,10.01),(4,10.04),(5,10.05)):
        r=host.evaluate(a,candle(i,price,position_quantity=100))
        assert not any(v.action=='replace_protective_stop' for v in r.evaluation.intents)
        assert r.state['active_stop']==pytest.approx(9.98)
        a=replace(a,state=r.state,status=r.status)
    for i,price in ((6,10.43),(7,10.44)):
        r=host.evaluate(a,candle(i,price,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
    assert r.state['active_stop']==pytest.approx(10.39)
    assert r.state['historical_hod_entry']['last_cleared_resistance']['upper']==10.42


def test_target_does_not_skip_nearest_resistance_to_force_an_advance():
    # The mathematically closest level may still be the old target's level.
    selected=H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,minimum_target=10.94)
    assert selected is None
    assert H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,minimum_target=11.49) is None


def test_target_advance_uses_latest_target_once_per_candle_and_rolls_back_on_rejection():
    import asyncio
    host,a,_=acquired()
    original=deepcopy(a.state['historical_hod_entry']['target'])
    # Cross two resistances in one candle: calculate one step from the current target.
    r=host.evaluate(a,candle(3,10.58,position_quantity=100))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(10.98)
    advanced=replace(a,state=r.state,status=r.status)
    r2=host.evaluate(advanced,candle(4,11.,position_quantity=100))
    next_target=next(v for v in r2.evaluation.intents if v.action=='replace_profit_target')
    assert next_target.metadata['profit_target_selection']['reference']==pytest.approx(10.96*1.05)
    assert next_target.profit_target_price==pytest.approx(11.49)
    assigned=S.AssignedLongMomentumStrategy([advanced])
    asyncio.run(assigned.on_intent_rejected(target,reasons=('broker rejected',),event_time=NOW))
    restored=assigned.assignments()[0].state
    assert restored['historical_hod_entry']['target']==original
    assert restored['structural_profit_targets']==[original['price']]


def test_red_close_and_intrabar_events_cannot_advance_target_or_apply_old_proposal():
    host,a,_=acquired()
    r=host.evaluate(a,candle(3,10.43,opened=10.44,position_quantity=100))
    assert not any(i.action=='replace_profit_target' for i in r.evaluation.intents)
    a.state['historical_hod_entry']['desired_target']={'price':11.49}
    a.state['historical_hod_entry']['desired_stop']=10.1
    for o in (replace(candle(3,10.43,position_quantity=100),evaluation_events=('market_data_update',)),
              candle(3,10.43,position_quantity=100,source_timeframe='5s')):
        r=host.evaluate(a,o)
        assert not any(i.action.startswith('replace_') for i in r.evaluation.intents)
    r=host.evaluate(a,candle(3,10.43,opened=10.44,position_quantity=100))
    assert not any(i.action=='replace_profit_target' for i in r.evaluation.intents)


def test_current_day_break_and_stop_advance_can_update_both_orders_together():
    host,a,_=acquired()
    a.state['historical_hod_entry']['desired_stop']=10.1
    # A current-day level, with no historical hold condition on its target.
    r=host.evaluate(a,candle(3,10.58,position_quantity=100))
    assert [i.action for i in r.evaluation.intents]==['replace_protective_stop','replace_profit_target']
    assert r.state['active_stop']==pytest.approx(10.1)
    assert r.evaluation.intents[-1].profit_target_price==pytest.approx(10.98)
    assert r.evaluation.intents[-1].metadata['profit_target_selection']['triggering_breakout']['lower']==10.55


def test_red_breakout_advances_target_on_later_non_red_holding_close():
    host,a,_=acquired()
    for i,price,opened in ((3,10.43,10.44),(4,10.44,10.45)):
        r=host.evaluate(a,candle(i,price,opened=opened,position_quantity=100))
        assert not any(v.action=='replace_profit_target' for v in r.evaluation.intents)
        a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(5,10.45,opened=10.44,position_quantity=100))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(10.98)
    assert target.metadata['profit_target_selection']['triggering_breakout']['upper']==10.42
    assert not r.state['historical_hod_entry']['target_breaks']


@pytest.mark.parametrize('failure', ['below','gap'])
def test_pending_red_breakout_is_cancelled_by_failure_or_missing_candle(failure):
    host,a,_=acquired()
    r=host.evaluate(a,candle(3,10.43,opened=10.44,position_quantity=100))
    a=replace(a,state=r.state,status=r.status)
    if failure=='below':
        r=host.evaluate(a,candle(4,10.41,position_quantity=100))
    else:
        r=host.evaluate(a,candle(5,10.45,position_quantity=100))
    assert not any(v.action=='replace_profit_target' for v in r.evaluation.intents)
    assert not r.state['historical_hod_entry']['target_breaks']


def test_sugp_041042_red_break_then_041043_green_confirmation():
    host,a,_=acquired()
    levels=tuple(dict(level(-1,low,high),oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
        for low,high in ((3.68,3.71),(3.83,3.85),(3.89,3.91),(4.0081982,4.0098018)))
    # Recorded SUGP prices; the position already has a $3.82 target.
    a.state.update(active_stop=3.61,structural_profit_targets=[3.82])
    a.state['historical_hod_entry'].update(target={'price':3.82,'level':levels[1]},
        stop=3.61,initial_risk=.04,best_close=3.6995,
        management_base={'lower':3.61,'tolerance':.01})
    a.state['historical_hod_state'].update(close=3.6995,rows=levels)
    r=host.evaluate(a,replace(candle(3,3.72,opened=3.7293,position_quantity=100),
        structural_resistance_levels=levels))
    assert not any(v.action=='replace_profit_target' for v in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,replace(candle(4,3.75,opened=3.7007,position_quantity=100),
        structural_resistance_levels=levels))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(4.02)
    assert target.metadata['profit_target_selection']['reference']==pytest.approx(3.84*1.05)


def test_unfilled_old_target_does_not_block_advance_but_flat_position_does():
    host,a,_=acquired()
    r=host.evaluate(a,candle(3,10.58,position_quantity=100))
    assert any(i.action=='replace_profit_target' for i in r.evaluation.intents)
    flat=host.evaluate(a,candle(3,10.58))
    assert not any(i.action=='replace_profit_target' for i in flat.evaluation.intents)


def test_failed_hold_does_not_raise_stop():
    host,a,_=acquired()
    for i,price in ((3,10.43),(4,10.41),(5,10.44)):
        r=host.evaluate(a,candle(i,price,position_quantity=100));a=replace(a,state=r.state,status=r.status)
    assert r.state['active_stop']==pytest.approx(9.98)


def test_trailing_uses_frozen_actual_entry_risk_when_historical_unavailable():
    host,a,_=acquired()
    o=replace(candle(3,10.2,position_quantity=100,average_price=10.05),structural_resistance_levels=rows()[2:])
    r=host.evaluate(a,o)
    assert r.state['historical_hod_entry']['initial_risk']==pytest.approx(.07)
    assert r.state['active_stop']==pytest.approx(10.13)


def test_swing_tolerance_is_management_only_and_requires_two_closes():
    active={'confirmed_at':1,'management_base':{'lower':10.,'tolerance':.02}}
    row={'local_events':[],'qualification':{'atr':.2}}
    bar={'close':9.99,'low':9.97,'high':10.1,'end':2}
    assert H.management(row,active,bar,H.DEFAULTS,.01)==''
    bar['close']=9.97
    assert H.management(row,active,bar,H.DEFAULTS,.01)==''
    assert H.management(row,active,bar,H.DEFAULTS,.01)=='protective_swing_failed'
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,9.975,position_quantity=100),source_values={}))
    assert r.evaluation.intents[0].reason=='protective_stop'


def test_failed_resistance_exit_waits_for_two_completed_100ms_candles():
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,10.40,opened=10.39,position_quantity=100),bar_high=10.43))
    a=replace(a,state=r.state,status=r.status)
    o=candle(4,10.38,opened=10.40,position_quantity=100)
    r=host.evaluate(a,o)
    assert not any(v.action=='exit' for v in r.evaluation.intents)
    armed=replace(a,state=r.state,status=r.status)
    stopped=host.evaluate(armed,replace(o,price=r.state['active_stop']-.01,
        observed_at=o.observed_at+timedelta(milliseconds=50),evaluation_events=('market_data_update',)))
    assert stopped.evaluation.intents[0].reason=='protective_stop'
    first=replace(o,source_timeframe='100ms',observed_at=o.observed_at+timedelta(milliseconds=100),price=10.37)
    r=host.evaluate(armed,first)
    assert not any(v.action=='exit' for v in r.evaluation.intents)
    armed=replace(armed,state=r.state,status=r.status)
    second=replace(first,observed_at=o.observed_at+timedelta(milliseconds=200),price=10.36)
    r=host.evaluate(armed,second)
    intent,=r.evaluation.intents
    assert intent.action=='exit' and intent.reason=='red_close_below_attempt_open'
    assert intent.quantity==100
    assert intent.metadata['failed_resistance_exit']['previous_bar']['open']==10.39
    assert intent.metadata['failed_resistance_exit']['intrabar_confirmation']['closes']==[10.37,10.36]
    assert not host.evaluate(a,replace(o,evaluation_events=('market_data_update',))).evaluation.intents
    # Equality, a non-red candle, and a gap must not arm this rule.
    for changed in (replace(o,price=10.39),replace(o,bar_open=10.37),candle(5,10.38,opened=10.40,position_quantity=100)):
        checked=host.evaluate(a,changed)
        assert not checked.state['historical_hod_entry'].get('pending_failed_attempt')


@pytest.mark.parametrize('closes,expected', [([10.37,10.36],True),([10.37,10.375],False),
    ([10.39,10.37],False),([10.37,10.37],False)])
def test_intrabar_failed_attempt_direction_and_recovery(closes,expected):
    _,_,o=ready()
    active={'pending_failed_attempt':dict(at_ms=round(o.observed_at.timestamp()*1000),trigger_close=10.38,closes=[]),
        'failed_resistance_exit':{}}
    first=replace(o,source_timeframe='100ms',observed_at=o.observed_at+timedelta(milliseconds=100),price=closes[0])
    assert not H.confirm_failed_attempt(active,replace(first,evaluation_events=('market_data_update',)))
    assert not H.confirm_failed_attempt(active,first)
    assert not H.confirm_failed_attempt(active,first)  # Duplicate cannot count twice.
    second=replace(first,observed_at=o.observed_at+timedelta(milliseconds=200),price=closes[1])
    assert H.confirm_failed_attempt(active,second)==expected
    assert 'pending_failed_attempt' not in active


def test_intrabar_failed_attempt_cannot_use_missing_or_late_candles():
    _,_,o=ready()
    active={'pending_failed_attempt':dict(at_ms=round(o.observed_at.timestamp()*1000),trigger_close=10.38,closes=[]),
        'failed_resistance_exit':{}}
    second=replace(o,source_timeframe='100ms',observed_at=o.observed_at+timedelta(milliseconds=200),price=10.36)
    assert not H.confirm_failed_attempt(active,second)
    assert not H.confirm_failed_attempt(active,replace(second,observed_at=o.observed_at+timedelta(milliseconds=300)))
    assert 'pending_failed_attempt' not in active


def test_red_lower_close_without_a_resistance_attempt_is_not_this_exit():
    host,a,_=acquired()
    r=host.evaluate(a,candle(3,10.2,opened=10.15,position_quantity=100))
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.14,opened=10.2,position_quantity=100))
    assert not any(v.reason=='red_close_below_attempt_open' for v in r.evaluation.intents)


def test_losing_entry_hod_exits_on_first_red_close_below_previous_open():
    active={'level':{'reference_kind':'hod','price':3.57,'lower':3.57,'upper':3.57},
        'confirmed_at':1,'management_base':{'lower':3.57,'tolerance':.01}}
    previous=dict(time=2,end=3,open=3.60,high=3.60,low=3.59,close=3.59)
    bar=dict(time=3,end=4,open=3.58,high=3.58,low=3.55,close=3.5562)
    assert H.management({},active,bar,H.DEFAULTS,.01,previous_bar=previous)=='red_close_below_attempt_open'
    assert active['failed_resistance_exit']['reference_kind']=='entry_hod'
    for changed in (dict(bar,close=3.575),dict(bar,open=3.55),dict(bar,time=4,end=5)):
        assert H.management({},deepcopy(active),changed,H.DEFAULTS,.01,previous_bar=previous)!='red_close_below_attempt_open'


@pytest.mark.parametrize('lower,upper,previous,bar,expected', [
    (3.83,3.85,(3.83,3.89,3.83,3.88),(3.82,3.86,3.81,3.8156),True),
    (3.94,3.9662931,(4.,4.,3.96,3.98),(3.99,4.,3.94,3.95),False),
    (4.09918,4.110822,(4.1,4.12,4.05,4.1),(4.1,4.13,4.0743,4.09),True),
])
def test_sugp_recorded_failed_attempt_closes(lower,upper,previous,bar,expected):
    def completed(values, start):
        return dict(zip(('open','high','low','close'),values),time=start,end=start+1)
    active={'confirmed_at':1,'management_base':{'lower':3.,'tolerance':.01}}
    resistance=dict(side='resistance',lower=lower,upper=upper,confirmed_at=1,level_id=1)
    reason=H.management({},active,completed(bar,3),H.DEFAULTS,.01,
        previous_bar=completed(previous,2),resistance_levels=[resistance])
    assert (reason=='red_close_below_attempt_open') == expected


def test_retest_tracks_frozen_band_until_actual_failure():
    active={'confirmed_at':1,'management_base':{'lower':3.,'tolerance':.01}}
    level=dict(side='resistance',lower=3.94,upper=3.9662931,confirmed_at=1,level_id=1)
    previous=dict(time=2,end=3,open=4.,high=4.,low=3.96,close=3.98)
    retest=dict(time=3,end=4,open=3.99,high=4.,low=3.94,close=3.95)
    assert H.management({},active,retest,H.DEFAULTS,.01,previous_bar=previous,resistance_levels=[level])==''
    # A changed/absent projection must not rewrite the encountered band's floor.
    changed=dict(level,lower=3.96,upper=3.98)
    holding=dict(time=4,end=5,open=3.95,high=3.96,low=3.94,close=3.94)
    assert H.management({},active,holding,H.DEFAULTS,.01,previous_bar=retest,resistance_levels=[changed])==''
    failed=dict(time=5,end=6,open=3.94,high=3.95,low=3.92,close=3.93)
    assert H.management({},active,failed,H.DEFAULTS,.01,previous_bar=holding)=='red_close_below_attempt_open'
    assert active['failed_resistance_exit']['level']==level
    assert active['failed_resistance_exit']['attempt_kind']=='failed_breakout'
    gap=dict(failed,time=6,end=7)
    assert H.management({},active,gap,H.DEFAULTS,.01,previous_bar=holding)==''


def test_engine_keeps_position_on_red_close_inside_broken_band():
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,10.44,opened=10.43,position_quantity=100),bar_low=10.41))
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.41,opened=10.44,position_quantity=100))
    assert not any(v.action=='exit' for v in r.evaluation.intents)
    assert r.state['historical_hod_entry']['resistance_attempts']['levels']


def test_reentry_uses_prior_body_high_excludes_wicks_and_current_candle():
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,10.2,position_quantity=100),bar_high=10.29))
    a=replace(a,state=r.state,status=S.AssignmentStatus.WATCHING)
    r=host.evaluate(a,candle(4,10.19));assert not r.evaluation.intents
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(5,10.23))
    assert r.evaluation.signals[0].action=='enter_long'
    assert r.evaluation.signals[0].metadata['entry_selection']['lower']==10.


def test_passive_episode_survives_late_assignment():
    host,a,o=ready();saved={}
    for i,price,tf in ((0,10.,'5s'),(1,10.25,'1s'),(2,10.01,'1s'),(3,10.04,'1s')):
        obs=candle(i,price,source_timeframe=tf)
        frame=SimpleNamespace(as_of=obs.observed_at,timeframe=tf,bar=dict(open=obs.bar_open,
            high=obs.bar_high,low=obs.bar_low,close=obs.price,volume=1000),
            indicator=dict(macd_line=.01,macd_signal=0,execution_vwap=9.8))
        saved=H.observe_frame(frame,saved,a.parameters,{'unified_levels':rows(),'session_high':10.3})
    market=deepcopy(obs.structural_detector_state);market['historical_hod_observation']=saved
    r=host.evaluate(replace(a,state={}),replace(obs,structural_detector_state=market))
    assert r.state['historical_hod_state']['body_high']==10.25
    assert r.evaluation.signals[0].action=='enter_long'


def test_candidate_compiles_as_backtest_only():
    from src.backend import historical_hod_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    payload,canvas,plan=C.build(configuration_base())
    validated,_,_=_build_configuration_release(configuration=payload,canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'],run_plan_id=plan,strategy_profile_id=C.PROFILE_ID)
    profile=next(p for p in validated['strategy']['profiles'] if p['profile_id']==C.PROFILE_ID)
    assert profile['parameters']['historical_hod_contract']==H.CONTRACT
    assert next(p for p in validated['run_plans']['plans'] if p['run_plan_id']==plan)['allowed_environments']==['backtest']


def test_point_projection_restores_bands_and_missing_lineage_fails_closed():
    o=candle(1,10.01)
    projected=tuple(dict(r,band_lower=r['lower'],band_upper=r['upper'],lower=r['price'],upper=r['price']) for r in rows())
    restored=H.selected_levels(replace(o,structural_resistance_levels=projected),H.DEFAULTS,o.observed_at.timestamp())
    assert restored[0]['lower']==10. and restored[0]['upper']==10.02
    bad=dict(rows()[0]);bad.pop('oldest_member_confirmed_at_ms')
    with pytest.raises(ValueError,match='provenance'):
        H.selected_levels(replace(o,structural_resistance_levels=(bad,)),H.DEFAULTS,o.observed_at.timestamp())


def test_rejection_requires_failed_recovery_and_volume_warning_alone_holds():
    active={'confirmed_at':1,'management_base':{'lower':9.,'tolerance':.02}}
    bar={'close':10.9,'low':10.85,'high':11.1,'end':2}
    rejection={'state':'rejection','level':{'side':'resistance','lower':11.,'upper':11.02}}
    assert H.management({'global_events':[rejection]},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=3,close=10.8,low=10.7)
    assert H.management({},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=4,close=10.85,low=10.8)
    recovery={'state':'lower_high_confirmed','level':{'price':10.9,'pivot_at':3.5,'confirmed_at':4}}
    assert H.management({'local_events':[recovery]},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=5,close=10.68,low=10.68)
    assert H.management({},active,bar,H.DEFAULTS,.01)=='resistance_rejection_failed_recovery'
    assert H.management({'volume_analysis':{'reversal_outcomes':[{'direction':'bearish',
        'outcome':'structural_reversal_confirmation'}]}},
        {'confirmed_at':1,'management_base':{'lower':10.,'tolerance':.02}},bar,H.DEFAULTS,.01)==''


def test_replay_passive_adapter_observes_both_clocks_before_assignment():
    import asyncio
    from unittest.mock import AsyncMock,patch
    from src.backend.replay_run_service import ReplayRunController
    from tests.test_structural_recovery import BOOK
    _,a,_=ready()
    fake=SimpleNamespace(definition=SimpleNamespace(configuration_revision={'payload':{'strategy':{'parameters':a.parameters}}},
        experimental_structure_book=BOOK['id']),_candle_detector_states={},
        _experimental_session_high=lambda ticker,at:10.3,
        _experimental_structure_snapshot=AsyncMock(return_value={'unified_levels':list(rows())}))
    with patch('src.backend.experimental_structure_book.resolve',return_value=BOOK):
        for i,tf in ((0,'5s'),(1,'1s'),(2,'1s'),(5,'5s')):
            frame=SimpleNamespace(timeframe=tf,ticker='TEST',as_of=NOW+timedelta(seconds=i),
                bar={'open':10.,'high':10.1,'low':10.,'close':10.1,'volume':1000},
                indicator={'macd_line':.01,'macd_signal':0.,'qmd_structure_session_high':10.3})
            asyncio.run(ReplayRunController._observe_episode_candle(fake,frame))
    stream=fake._candle_detector_states['TEST']['structural_recovery']
    assert stream['row']['contract']=='structural-candle-detector-10'
    assert stream['row']['sequence']==2
    passive=stream['historical_hod_observation']
    assert passive['macd_at']==(NOW+timedelta(seconds=5)).timestamp()
    assert passive['body_high']==10.1 and passive['hod']==10.3


def test_pending_acquisition_expires_and_rejected_replacement_retries():
    host,a,o=ready();r=host.evaluate(a,o)
    pending=replace(a,state=r.state,status=r.status)
    expired=host.evaluate(pending,candle(3,10.04))
    assert expired.evaluation.intents[0].action=='cancel_entry'
    a=replace(pending,status=S.AssignmentStatus.MANAGING)
    for i,p in ((3,10.43),(4,10.44)):
        r=host.evaluate(a,candle(i,p,position_quantity=100));a=replace(a,state=r.state,status=r.status)
    # The shared rejection path restores this value; desired state must survive.
    a.state['active_stop']=r.evaluation.intents[0].metadata['previous_stop']
    retry=host.evaluate(a,candle(6,10.46,position_quantity=100))
    assert retry.evaluation.intents[0].action=='replace_protective_stop'
    assert retry.evaluation.intents[0].invalidation_price==pytest.approx(10.39)


def test_entry_fill_marks_episode_used_before_next_price_observation():
    import asyncio
    host,a,o=ready();r=host.evaluate(a,o)
    assigned=S.AssignedLongMomentumStrategy([replace(a,state=r.state,status=r.status)])
    fill=SimpleNamespace(assignment_id=a.assignment_id,state='FILLED',action='enter_long',
        fill_incremental_quantity=100.,filled_quantity=100.,updated_at=o.observed_at)
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=100.))
    assert assigned.assignments()[0].state['historical_hod_state']['used_episode']


def test_historical_candidate_requires_certified_v6_ticker():
    from datetime import time
    from unittest.mock import patch
    from src.backend.replay_run_service import ReplayRunDefinition,RunMode
    from tests.test_structural_recovery import BOOK
    _,a,_=ready()
    args=dict(session_date=NOW.date(),start_time=time(9,30),mode=RunMode.BACKTEST,tickers=('TEST',),
        configuration_revision={'revision_id':'historical-test','payload':{'strategy':{'parameters':a.parameters}}})
    with pytest.raises(ValueError,match='explicitly selected certified V6'):
        ReplayRunDefinition(**args)
    book=dict(BOOK,ticker='TEST',start=NOW.date().isoformat(),end=NOW.date().isoformat())
    with patch('src.backend.experimental_structure_book.resolve',return_value=book):
        assert ReplayRunDefinition(**args,experimental_structure_book=BOOK['id']).experimental_structure_fingerprint==BOOK['fingerprint']


def test_runtime_places_broker_stop_and_full_target(tmp_path):
    import asyncio
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        _,a,o=ready()
        journal=TradingJournal(tmp_path/'journal.sqlite3')
        broker=T.SimulatedBrokerAdapter(['sim'],mode=T.TradingMode.BACKTEST)
        runtime=T.TradingRuntime(T.RunConfig(mode=T.RunMode.BACKTEST,strategy_id=S.STRATEGY_ID,
            strategy_revision=47,account_ids=('sim',),anchor_date=NOW.date(),run_id='historical-hod-test'),
            broker,S.AssignedLongMomentumStrategy([a]),journal,
            intent_planner=T.RuntimeIbkrStrategyOrderPlanner({'TEST':T.InstrumentContract('ibkr:123',123,'TEST','STK','USD')},
                strategy_id=S.STRATEGY_ID,strategy_revision=47))
        try:
            await runtime.initialize()
            stamp=o.observed_at-timedelta(milliseconds=1)
            await broker.on_market_event(T.QuoteEvent(ask_exchange=11,ask_price=o.ask,ask_size=10000,
                bid_exchange=12,bid_price=o.bid,bid_size=10000,conditions=(),indicators=(),
                ingest_ts=stamp,raw={'conid':123},sequence=1,source='test',tape=3,ticker='TEST',ts=stamp))
            await runtime.process_strategy_observation(o)
            orders=await broker.live_orders()
            assert len([r for r in orders if r.orderType=='STP'])==1
            assert len([r for r in orders if r.orderType=='LMT' and r.parentId])==1
        finally:
            journal.close()
    asyncio.run(run())
