from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.trading_runtime import historical_hod as H, strategy_engine as S
from tests.test_structural_recovery import parameters, observation, level, NOW


def rows():
    return tuple(dict(level(-1,x,x+.02),oldest_member_confirmed_at_ms=
        (NOW.timestamp()-86400)*1000)
        for x in (10.,10.4,10.55,10.95,11.5,12.))


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
    p.update(historical_hod_contract=H.CONTRACT,historical_hod=dict(H.DEFAULTS,forming_macd_entry_enabled=0,early_green_stop_enabled=0))
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


def green_stop_position():
    host,a,_=acquired()
    a.parameters['historical_hod']['early_green_stop_enabled']=1
    a.state['historical_hod_state']['rising_green_run']=[]
    for i,opened,close in ((3,10.05,10.07),(4,10.08,10.10),(5,10.11,10.14)):
        r=host.evaluate(a,candle(i,close,opened=opened,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
    return host,a,r


def test_three_green_stop_waits_for_third_close_and_uses_second_close():
    host,a,r=green_stop_position()
    stop,=r.evaluation.intents
    assert stop.action=='replace_protective_stop'
    assert stop.reason=='three_green_second_close'
    assert stop.invalidation_price==pytest.approx(10.10)
    assert len(stop.metadata['early_green_stop']['candles'])==3
    # A longer run cannot ratchet this one-time initial stop on each candle.
    r=host.evaluate(a,candle(6,10.18,opened=10.15,position_quantity=100))
    assert r.state['active_stop']==pytest.approx(10.10)


@pytest.mark.parametrize('interruption',['non_rising_close','red','gap','disabled'])
def test_three_green_requires_rising_closes_and_contiguous_post_entry_bars(interruption):
    host,a,_=acquired()
    a.parameters['historical_hod']['early_green_stop_enabled']=int(interruption!='disabled')
    a.state['historical_hod_state']['rising_green_run']=[]
    for i,opened,close in ((3,10.05,10.07),(4,10.08,10.10),(5,10.11,10.14)):
        if i==4 and interruption=='non_rising_close':opened,close=10.05,10.07
        if i==4 and interruption=='red':opened=10.12
        if i==4 and interruption=='gap':continue
        r=host.evaluate(a,candle(i,close,opened=opened,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
    assert not a.state['historical_hod_entry'].get('early_green_stop')


@pytest.mark.parametrize('overlap',[0.,.02])
def test_three_green_allows_equal_opens_and_overlapping_bodies(overlap):
    host,a,_=acquired()
    a.parameters['historical_hod']['early_green_stop_enabled']=1
    a.state['historical_hod_state']['rising_green_run']=[]
    for i,previous,close in ((3,10.04,10.07),(4,10.07,10.10),(5,10.10,10.14)):
        r=host.evaluate(a,candle(i,close,opened=previous-overlap,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
    assert r.state['active_stop']==pytest.approx(10.10)
    assert r.evaluation.intents[0].reason=='three_green_second_close'


def test_pattern_can_begin_before_entry_and_finish_after_it():
    host,a,_=acquired()
    a.parameters['historical_hod']['early_green_stop_enabled']=1
    for i,close in ((3,10.07),(4,10.10)):
        r=host.evaluate(a,candle(i,close,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
    assert r.state['active_stop']==pytest.approx(10.07)
    assert r.state['historical_hod_entry']['early_green_stop']['candles'][0]['end']==(NOW+timedelta(seconds=2)).timestamp()


def test_entry_uses_completed_pattern_across_macd_episode_start():
    host,a,_=ready()
    a.parameters['historical_hod']['early_green_stop_enabled']=1
    a.state['historical_hod_state'].update(episode=None,macd_positive=False)
    for obs in (candle(2,10.03),candle(3,10.05),candle(3,10.05,source_timeframe='5s')):
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.08))
    entry,=r.evaluation.intents
    assert entry.action=='enter_long' and entry.invalidation_price==pytest.approx(10.05)
    early=r.state['historical_hod_entry']['early_green_stop']
    assert early['candles'][0]['end']<r.state['historical_hod_entry']['episode']


def test_already_marketable_new_stop_exits_at_pattern_confirmation():
    host,a,_=acquired()
    a.parameters['historical_hod']['early_green_stop_enabled']=1
    for i,close in ((3,10.07),(4,10.10)):
        obs=candle(i,close,position_quantity=100)
        if i==4:obs=replace(obs,bid=10.07,ask=10.10)
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
    exit,=r.evaluation.intents
    assert exit.action=='exit' and exit.reason=='protective_stop'
    assert exit.metadata['early_stop_marketable_at_confirmation']
    assert exit.invalidation_price==pytest.approx(10.07)


def test_stop_fill_can_reclaim_without_previous_completed_close_below_level():
    host,a,_=green_stop_position()
    H.record_early_stop_fill(a.state,NOW+timedelta(seconds=5.2),'protective_stop')
    a=replace(a,status=S.AssignmentStatus.REENTRY_COOLDOWN)
    for obs in (candle(5,10.14,source_timeframe='5s'),candle(6,10.15)):
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
    obs=replace(candle(6,10.16,opened=10.16),observed_at=NOW+timedelta(seconds=6.1),
        source_timeframe='',evaluation_events=('market_data_update',),bar_high=None)
    r=host.evaluate(a,obs)
    assert r.evaluation.intents[0].reason=='stopped_level_reclaim'


def test_next_resistance_retires_pattern_arming_without_waiting_for_hold():
    host,a,_=green_stop_position()
    r=host.evaluate(a,candle(6,10.43,opened=10.15,position_quantity=100))
    assert r.state['historical_hod_entry']['early_green_graduated']
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(7,10.45,opened=10.44,position_quantity=100))
    assert r.state['active_stop']==pytest.approx(10.39)
    H.record_early_stop_fill(r.state,NOW+timedelta(seconds=7.1),'protective_stop')
    assert 'early_stop_reentry' not in r.state


@pytest.mark.parametrize('replacement',['not_yet','accepted','rejected'])
def test_resistance_cross_records_stop_hit_until_stop_actually_changes(replacement):
    import asyncio
    host,a,_=green_stop_position()
    r=host.evaluate(a,candle(6,10.43,opened=10.15,position_quantity=100))
    a=replace(a,state=r.state,status=r.status)
    assert a.state['historical_hod_entry']['early_green_graduated']
    assert a.state['active_stop']==pytest.approx(10.10)
    if replacement!='not_yet':
        r=host.evaluate(a,candle(7,10.45,opened=10.44,position_quantity=100))
        a=replace(a,state=r.state,status=r.status)
        assert a.state['active_stop']==pytest.approx(10.39)
        if replacement=='rejected':
            # Same restoration performed by the shared rejection callback.
            a.state['active_stop']=r.evaluation.intents[0].metadata['previous_stop']
    assigned=S.AssignedLongMomentumStrategy([a])
    fill=SimpleNamespace(assignment_id=a.assignment_id,state='FILLED',action='exit',
        fill_incremental_quantity=100.,filled_quantity=100.,updated_at=NOW+timedelta(seconds=7.2),
        fill_role='protective_stop',reentry_after_fill=True)
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=0.))
    saved=assigned.assignments()[0].state.get('early_stop_reentry')
    assert bool(saved)==(replacement!='accepted')
    if saved:assert saved['price']==pytest.approx(10.10)


@pytest.mark.parametrize('role,reason,remember',[
    ('protective_stop','',True),('managed_exit','protective_stop',True),
    ('profit_target','',False),('managed_exit','red_close_below_attempt_open',False)])
def test_execution_fill_records_only_initial_stop_hits(role,reason,remember):
    import asyncio
    _,a,_=green_stop_position()
    a.state['last_exit_reason']=reason
    assigned=S.AssignedLongMomentumStrategy([a])
    fill=SimpleNamespace(assignment_id=a.assignment_id,state='FILLED',action='exit',
        fill_incremental_quantity=100.,filled_quantity=100.,updated_at=NOW+timedelta(seconds=5.2),
        fill_role=role,reentry_after_fill=True)
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=0.))
    state=assigned.assignments()[0].state
    assert bool(state.get('early_stop_reentry'))==remember
    if remember:assert state['early_stop_reentry']['price']==pytest.approx(10.10)


def test_reentry_record_clears_only_on_actual_acquisition_fill():
    import asyncio
    host,a,_=green_stop_position()
    H.record_early_stop_fill(a.state,NOW+timedelta(seconds=5.2),'protective_stop')
    a=replace(a,status=S.AssignmentStatus.REENTRY_COOLDOWN)
    for obs in (candle(5,10.09,source_timeframe='5s'),candle(6,10.09),candle(7,10.12)):
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
    obs=replace(candle(7,10.12,opened=10.12),observed_at=NOW+timedelta(seconds=7.1),
        source_timeframe='',evaluation_events=('market_data_update',),bar_high=None)
    r=host.evaluate(a,obs)
    assert r.evaluation.intents[0].action=='enter_long'
    assigned=S.AssignedLongMomentumStrategy([replace(a,state=r.state,status=r.status)])
    # A zero-quantity update cannot consume the remembered stop.
    fill=SimpleNamespace(assignment_id=a.assignment_id,state='FILLED',action='enter_long',
        fill_incremental_quantity=0.,filled_quantity=0.,updated_at=obs.observed_at)
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=0.))
    assert assigned.assignments()[0].state.get('early_stop_reentry')
    fill.fill_incremental_quantity=100.;fill.filled_quantity=100.
    asyncio.run(assigned.on_order_group_update(fill,aggregate_position_quantity=100.))
    assert 'early_stop_reentry' not in assigned.assignments()[0].state


@pytest.mark.parametrize('failure',[None,'open_equal','open_below','late','spread','episode_ended','new_episode'])
@pytest.mark.parametrize('graduated',[False,True])
def test_stopped_level_reentry_close_then_next_open_with_current_gates(failure,graduated):
    import json
    host,a,_=green_stop_position()
    a.state['historical_hod_entry']['early_green_graduated']=graduated
    H.record_early_stop_fill(a.state,NOW+timedelta(seconds=5.2),'protective_stop')
    # Checkpoint serialization must preserve remembered prices and episode identity.
    a=replace(a,state=json.loads(json.dumps(a.state)),status=S.AssignmentStatus.REENTRY_COOLDOWN)
    for obs in (candle(5,10.09,source_timeframe='5s'),candle(6,10.09),candle(7,10.12)):
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
    assert not r.evaluation.intents  # The close alone cannot buy.
    if failure in ('episode_ended','new_episode'):
        obs=replace(candle(7,10.12,source_timeframe='5s'),macd_line=-.1)
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
        if failure=='new_episode':
            r=host.evaluate(a,candle(8,10.12,source_timeframe='5s'))
            a=replace(a,state=r.state,status=r.status)
        assert 'early_stop_reentry' not in a.state
    opened=10.10 if failure=='open_equal' else 10.09 if failure=='open_below' else 10.12
    obs=replace(candle(7,10.12,opened=opened),observed_at=NOW+timedelta(seconds=8.1 if failure=='late' else 7.1),
        source_timeframe='',evaluation_events=('market_data_update',),bar_high=None)
    if failure=='spread':obs=replace(obs,ask=10.30)
    r=host.evaluate(a,obs)
    if failure:
        assert not r.evaluation.intents
    else:
        entry,=r.evaluation.intents
        assert entry.action=='enter_long' and entry.reason=='stopped_level_reclaim'
        assert entry.invalidation_price==pytest.approx(10.10)
        assert r.state['historical_hod_entry']['early_green_stop']['price']==pytest.approx(10.10)
        proof=entry.metadata['entry_breakout_confirmation']['stopped_level_reclaim']
        assert proof['at']==(NOW+timedelta(seconds=7)).timestamp()
        assert proof['next_open']==10.12
        assert r.state['early_stop_reentry']['price']==pytest.approx(10.10)
    if failure in ('open_equal','open_below','spread'):
        a=replace(a,state=r.state,status=r.status)
        r=host.evaluate(a,replace(obs,observed_at=NOW+timedelta(seconds=7.2),bar_open=10.12,ask=10.13))
        assert not r.evaluation.intents  # Never replay a rejected opening later.


def test_forming_macd_preserves_authoritative_ema_and_does_not_compound():
    fast,slow,signal=9.8,10.,-.12
    d={}
    for i,price in enumerate((10.1,10.2,10.4)):
        fast=2/13*price+11/13*fast
        slow=2/27*price+25/27*slow
        line=fast-slow;signal=.2*line+.8*signal
        o=replace(candle(i*5,price,source_timeframe='5s'),macd_line=line,macd_signal=signal)
        H.forming_macd(o,d)
        if i:
            assert d['completed_macd']['slow']==pytest.approx(slow,abs=1e-12)
            before=deepcopy(d)
            for second,px in ((1,10.5),(2,10.6),(3,10.5)):
                preview=H.forming_macd(candle(i*5+second,px),d)
                expected=2/13*px+11/13*fast-(2/27*px+25/27*slow)
                assert preview['line']==pytest.approx(expected,abs=1e-12)
                assert preview['signal']==pytest.approx(.2*expected+.8*signal,abs=1e-12)
                assert d==before
            assert H.forming_macd(candle(i*5,price),d)['line']==line
            assert H.forming_macd(candle(i*5+6,price),d)['line'] is None


@pytest.mark.parametrize('failure',[None,'no_break','red','bearish','stale','no_base'])
def test_forming_macd_and_confirmed_resistance_are_both_required(failure):
    host,a,o=ready()
    a.parameters['historical_hod']['forming_macd_entry_enabled']=1
    d=a.state['historical_hod_state']
    d.update(episode=None,macd_positive=False,completed_macd=dict(
        at=NOW.timestamp(),slow=10.,line=-.001,signal=0.))
    if failure=='no_break':o=candle(2,10.01)
    if failure=='red':o=candle(2,10.04,opened=10.05)
    if failure=='bearish':d['completed_macd']['signal']=.1
    if failure=='stale':d['completed_macd']['at']-=5
    if failure=='no_base':d['completed_macd'].pop('slow')
    r=host.evaluate(a,o)
    if failure:
        assert not r.evaluation.intents
    else:
        assert r.evaluation.intents[0].action=='enter_long'
        assert r.evaluation.signals[0].metadata['macd']['kind']=='forming'
        assert r.state['historical_hod_state']['completed_macd']['line']<0


def test_forming_reversal_does_not_exit_but_same_timestamp_completed_reversal_does():
    host,a,o=acquired()
    a.parameters['historical_hod']['forming_macd_entry_enabled']=1
    a.state['historical_hod_state']['completed_macd']=dict(at=NOW.timestamp(),slow=10.,line=.01,signal=.2)
    r=host.evaluate(a,candle(5,10.05,position_quantity=100))
    assert not any(i.action=='exit' for i in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    obs=replace(candle(5,10.05,source_timeframe='5s',position_quantity=100),macd_line=0.,macd_signal=.1)
    r=host.evaluate(a,obs)
    assert r.evaluation.signals[0].reason=='macd_episode_ended'


def test_passive_forming_macd_matches_direct_observer_and_survives_checkpoint():
    import json
    _,a,_=ready()
    a.parameters['historical_hod']['forming_macd_entry_enabled']=1
    saved={};direct={'session':NOW.date().isoformat()}
    observations=[replace(candle(0,10.,source_timeframe='5s'),macd_line=-.001,macd_signal=0.),
        replace(candle(5,10.,source_timeframe='5s'),macd_line=-.001,macd_signal=0.),
        candle(6,10.01),candle(7,10.04)]
    for obs in observations:
        frame=SimpleNamespace(as_of=obs.observed_at,timeframe=obs.source_timeframe,
            bar=dict(open=obs.bar_open,high=obs.bar_high,low=obs.bar_low,close=obs.price,volume=obs.bar_volume),
            indicator=dict(macd_line=obs.macd_line,macd_signal=obs.macd_signal,execution_vwap=obs.execution_vwap))
        saved=H.observe_frame(frame,saved,a.parameters,{'unified_levels':rows(),'session_high':10.3})
        saved=json.loads(json.dumps(saved))
        H.observe(obs,direct,a.parameters['historical_hod'])
        for key in ('macd_at','macd_line','macd_signal','macd_positive','episode','completed_macd'):
            assert saved[key]==direct[key]
    assert saved['macd_kind']=='forming' and saved['macd_positive']


def test_chart_references_exist_before_macd_gate_and_match_entry_boundary():
    host,a,o=ready()
    a.state['historical_hod_state'].update(episode=None,macd_positive=False)
    r=host.evaluate(a,o)
    ref=r.evaluation.signals[0].metadata['historical_hod_reference']
    assert ref['hod']==10.3 and ref['resistance_upper']==10.02 and ref['changed']
    assert not r.evaluation.intents
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(3,10.04))
    assert not r.evaluation.signals[0].metadata['historical_hod_reference']['changed']
    host,a,o=ready();r=host.evaluate(a,o)
    metadata=r.evaluation.signals[0].metadata
    assert metadata['historical_hod_reference']['resistance_upper']==metadata['entry_selection']['upper']
    assert metadata['unified_structural_trigger']['current_snapshot']['levels'][0]['entry_boundary']==10.02


@pytest.mark.parametrize('failure',[None,'retired','lost','expired','bid_below'])
def test_recent_breakout_can_wait_for_macd_but_must_remain_valid(failure):
    host,a,o=ready()
    a.parameters['historical_hod']['recent_breakout_seconds']=3 if failure=='expired' else 30
    a.state['historical_hod_state'].update(episode=None,macd_positive=False)
    for i in range(2,7):
        price=10.01 if failure=='lost' and i==3 else 10.05
        obs=candle(i,price,opened=10.10 if failure=='lost' and i==4 else price-.01)
        r=host.evaluate(a,obs);a=replace(a,state=r.state,status=r.status)
        assert not r.evaluation.intents
    r=host.evaluate(a,candle(6,10.05,source_timeframe='5s'))
    a=replace(a,state=r.state,status=r.status)
    obs=candle(7,10.05)
    if failure=='retired':
        a.state['historical_hod_state']['rows']=[]
    if failure=='bid_below': obs=replace(obs,bid=10.01,ask=10.05)
    r=host.evaluate(a,obs)
    if failure in (None,'retired'):
        assert r.evaluation.intents[0].action=='enter_long'
        assert r.evaluation.signals[0].metadata['entry_breakout_confirmation']['breakout']['at']==(NOW+timedelta(seconds=2)).timestamp()
    else:
        assert not r.evaluation.intents


def official_band(o,upper=10.8,lower=9.):
    return dict(source='sip',session_date='2026-08-21',upper=upper,lower=lower,
        effective_at_ms=o.observed_at.timestamp()*1000,available_at_ms=o.observed_at.timestamp()*1000)


def test_backtest_estimate_enters_and_promotes_target_without_official_bands():
    from src.trading_runtime.estimated_luld import reference_from_indicator
    host,a,o=ready();a.parameters['historical_hod']['regular_luld_enabled']=1
    a.parameters['historical_hod']['backtest_luld_estimation_enabled']=1
    o=replace(o,previous_close=2.,backtest_luld_reference=reference_from_indicator(
        dict(qmd_structure_luld_lower=9.,qmd_structure_luld_upper=11.),o.observed_at))
    a.parameters['historical_hod']['backtest_luld_estimation_enabled']=0
    assert host.evaluate(a,o).evaluation.signals[0].reason=='official_luld_unavailable'
    a.parameters['historical_hod']['backtest_luld_estimation_enabled']=1
    r=host.evaluate(a,o)
    assert r.evaluation.intents[0].action=='enter_long'
    assert r.evaluation.intents[0].profit_target_price==pytest.approx(11.97)
    assert r.state['historical_hod_entry']['target']['selection_method']=='estimated_luld'
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    obs=candle(35,10.1,opened=10.2,position_quantity=100,source_timeframe='100ms',previous_close=2.)
    obs=replace(obs,backtest_luld_reference=reference_from_indicator(
        dict(qmd_structure_luld_lower=9.9,qmd_structure_luld_upper=12.1),obs.observed_at))
    r=host.evaluate(a,obs)
    target=next(i for i in r.evaluation.intents if i.action=='replace_profit_target')
    assert target.reason=='estimated_luld_target_update'
    assert target.profit_target_price==pytest.approx(13.16)


@pytest.mark.parametrize('prior,reason',[(None,'regular_previous_close_unavailable'),(.7499,'regular_previous_close_below_minimum'),(.75,'historical_hod_entry')])
def test_regular_luld_entry_and_prior_close_filter(prior,reason):
    host,a,o=ready()
    a.parameters['historical_hod']['regular_luld_enabled']=1
    o=replace(o,previous_close=prior,official_luld_band=official_band(o))
    r=host.evaluate(a,o)
    assert r.evaluation.signals[0].reason==reason
    if prior==.75:
        assert r.evaluation.intents[0].profit_target_price==pytest.approx(10.77)


@pytest.mark.parametrize('patch',[{'source':'estimated'},{'effective_at_ms':0},
    {'available_at_ms':NOW.timestamp()*1000+100000},{'session_date':'2026-08-20'},{'lower':11.}])
def test_regular_luld_rejects_invalid_band(patch):
    host,a,o=ready();a.parameters['historical_hod']['regular_luld_enabled']=1
    o=replace(o,previous_close=1.,official_luld_band=dict(official_band(o),**patch))
    assert host.evaluate(a,o).evaluation.signals[0].reason=='official_luld_unavailable'


def test_regular_luld_updates_intrabar_on_red_candles_and_can_move_down():
    host,a,o=ready();a.parameters['historical_hod']['regular_luld_enabled']=1
    o=replace(o,previous_close=1.,official_luld_band=official_band(o))
    r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    for i,upper in ((3,11.),(4,10.7)):
        obs=candle(i,10.1,opened=10.2,position_quantity=100,source_timeframe='100ms',previous_close=1.)
        obs=replace(obs,official_luld_band=official_band(obs,upper))
        r=host.evaluate(a,obs)
        target=next(x for x in r.evaluation.intents if x.action=='replace_profit_target')
        assert target.profit_target_price==pytest.approx(upper-.03)
        a=replace(a,state=r.state,status=r.status)


def test_regular_luld_exit_at_buffer_and_missing_data_preserves_position():
    host,a,o=acquired();a.parameters['historical_hod']['regular_luld_enabled']=1
    obs=candle(3,10.4,position_quantity=100,source_timeframe='100ms')
    assert not host.evaluate(a,obs).evaluation.intents
    obs=replace(obs,official_luld_band=official_band(obs,upper=10.42))
    assert host.evaluate(a,obs).evaluation.signals[0].reason=='luld_buffer_reached'


@pytest.mark.parametrize('hour',[8,21])
def test_extended_hours_do_not_use_luld_policy(hour):
    host,a,o=ready();a.parameters['historical_hod']['regular_luld_enabled']=1
    obs=replace(o,observed_at=o.observed_at.replace(hour=hour),official_luld_band={})
    r=host.evaluate(a,obs)
    assert 'regular_session_policy' not in r.evaluation.signals[0].metadata


@pytest.mark.parametrize('hour,minute,expected_luld',[(13,29,True),(19,59,False)])
def test_open_position_changes_target_policy_at_session_boundary(monkeypatch,hour,minute,expected_luld):
    import sys
    from tests import test_structural_recovery as fixtures
    at=NOW.replace(hour=hour,minute=minute,second=57)
    monkeypatch.setattr(fixtures,'NOW',at)
    monkeypatch.setattr(sys.modules[__name__],'NOW',at)
    host,a,o=ready()
    a.parameters['historical_hod']['regular_luld_enabled']=1
    a.parameters['strategy_behavior'].update(eligible_sessions=['premarket','regular','after_hours'],entry_cutoff_time='19:45:00',flatten_time='19:55:00')
    o=replace(o,previous_close=1.,official_luld_band=official_band(o))
    r=host.evaluate(a,o)
    assert r.evaluation.intents[0].action=='enter_long'
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    obs=candle(3,10.1,position_quantity=100,previous_close=1.)
    obs=replace(obs,official_luld_band=official_band(obs))
    r=host.evaluate(a,obs)
    target=next(x for x in r.evaluation.intents if x.action=='replace_profit_target')
    assert (target.reason=='official_luld_target_update')==expected_luld
    assert (r.state['historical_hod_entry']['target'].get('selection_method')=='official_luld')==expected_luld


def test_entry_close_non_red_and_complete_broker_protection():
    host,a,o=ready();r=host.evaluate(a,o)
    intent,=r.evaluation.intents
    assert intent.action=='enter_long'
    assert intent.invalidation_price==pytest.approx(9.98)
    assert intent.profit_target_price==pytest.approx(10.94)
    assert len(intent.protection_profile.slices)==1
    assert intent.metadata['mandatory_broker_target']
    assert intent.resolved_execution_policy().envelope.deadline_ms==0
    assert S.strategy_rule_timeframes(a.parameters)=={'100ms','1s','5s'}
    assert host.evaluate(a,replace(o,bar_open=10.06)).evaluation.signals[0].reason=='red_breakout_candle'
    assert host.evaluate(a,replace(o,bar_open=o.price)).evaluation.signals[0].action=='enter_long'
    assert not host.evaluate(a,replace(o,evaluation_events=('market_data_update',))).evaluation.intents
    assert not host.evaluate(a,replace(o,price=10.02,bar_high=10.1)).evaluation.intents


def test_compiled_settings_invalidate_on_nested_changes_and_validate_again():
    from unittest.mock import patch
    host,a,o=ready()
    with patch.object(S,'resolve_long_momentum_parameters',wraps=S.resolve_long_momentum_parameters) as resolve:
        host.evaluate(a,o)
        assert resolve.call_count==0
        a.parameters['historical_hod']['stop_buffer_bps']=20.
        assert host.evaluate(a,o).evaluation.intents[0].invalidation_price==pytest.approx(9.97)
        assert resolve.call_count==1
        a.parameters['historical_hod']['stop_buffer_bps']=-1.
        with pytest.raises(ValueError,match='finite and positive'):
            host.evaluate(a,o)


@pytest.mark.parametrize('close,expected', [(10.0299,False),(10.03,True),(10.04,True)])
def test_buffered_entry_accepts_exact_offset(close,expected):
    host,a,o=ready()
    a.parameters['historical_hod']['entry_breakout_offset']=.01
    r=host.evaluate(a,replace(o,price=close,bar_open=10.02))
    assert bool(r.evaluation.intents)==expected
    assert not host.evaluate(a,replace(o,price=close,bar_open=close+.01)).evaluation.intents


def test_buffered_entry_can_cross_after_unbuffered_band_was_cleared():
    host,a,o=ready()
    a.parameters['historical_hod']['entry_breakout_offset']=.01
    r=host.evaluate(a,replace(o,price=10.025,bar_open=10.02))
    assert not r.evaluation.intents
    a=replace(a,state=r.state,status=r.status)
    assert host.evaluate(a,candle(3,10.03)).evaluation.intents


def test_market_evidence_and_prior_states_remain_unchanged_across_observations():
    host,a,o=acquired()
    retained=[]
    for index,price in ((3,10.44),(4,10.45),(5,10.58),(6,10.54),(7,10.52)):
        obs=candle(index,price,position_quantity=100,average_price=10.04)
        retained.append((a.state,deepcopy(a.state)))
        frozen=deepcopy(obs.structural_detector_state)
        r=host.evaluate(a,obs)
        assert obs.structural_detector_state==frozen
        a=replace(a,state=r.state,status=r.status)
    assert all(state==frozen for state,frozen in retained)


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
    assert host.evaluate(replace(a,state=no_target),replace(o,structural_resistance_levels=(rows()[0],))).evaluation.signals[0].reason=='qualified_target_unavailable'


def test_sugp_entry_accepts_fresh_ask_above_old_close_based_cap():
    host,a,_=ready()
    levels=tuple(dict(level(-1,low,high),price=price,
        oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
        for low,price,high in ((4.2736451,4.2745,4.290858),(4.4061186,4.44,4.440888),(4.65,4.66,4.67)))
    a.state['historical_hod_state'].update(close=4.26,rows=levels,hod=4.34)
    o=replace(candle(2,4.3003,opened=4.27),bid=4.30,ask=4.31,execution_vwap=4.,
        structural_session_high=4.34,structural_resistance_levels=levels)
    o.structural_detector_state['row']['local_swings']=[dict(side='support',lower=4.25,price=4.255,
        upper=4.26,pivot_at=NOW.timestamp()-10,confirmed_at=NOW.timestamp()-5)]
    r=host.evaluate(a,o)
    intent,=r.evaluation.intents
    assert intent.action=='enter_long'
    assert intent.metadata['maximum_buy_price']==pytest.approx(4.31*1.0015)
    assert intent.profit_target_price==pytest.approx(4.68)
    assert intent.metadata['profit_target_selection']['placement']=='above_upper_band'
    assert not host.evaluate(a,replace(o,ask=4.40)).evaluation.intents


def test_target_placement_switches_at_five_percent_reference():
    broken={'price':10.,'upper':10.01}
    near=dict(level(-1,10.39,10.413),price=10.4,oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
    far=dict(level(-1,10.59,10.61),price=10.6,oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
    selected=H.target_selection([near],broken,10.1,H.DEFAULTS,.01,session='2026-08-21')
    assert selected['price']==pytest.approx(10.43)
    assert selected['placement']=='above_upper_band'
    selected=H.target_selection([far],broken,10.1,H.DEFAULTS,.01,session='2026-08-21')
    assert selected['price']==pytest.approx(10.58)
    assert selected['placement']=='below_lower_band'
    # Eligibility must use the actual upper-band placement, not the old lower-band price.
    assert H.target_selection([near],broken,10.40,H.DEFAULTS,.01,session='2026-08-21')['price']==pytest.approx(10.43)


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
    selected=H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,session='2026-08-21',minimum_target=10.94)
    assert selected is None
    assert H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,session='2026-08-21',minimum_target=11.49) is None


def test_target_advance_uses_next_historical_once_per_candle_and_rolls_back_on_rejection():
    import asyncio
    host,a,_=acquired()
    original=deepcopy(a.state['historical_hod_entry']['target'])
    # Cross two resistances in one candle: use the first historical still above it.
    r=host.evaluate(a,candle(3,10.58,position_quantity=100))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(11.49)
    advanced=replace(a,state=r.state,status=r.status)
    r2=host.evaluate(advanced,candle(4,11.,position_quantity=100))
    next_target=next(v for v in r2.evaluation.intents if v.action=='replace_profit_target')
    assert next_target.metadata['profit_target_selection']['reference']==pytest.approx(11.51*1.05)
    assert next_target.profit_target_price==pytest.approx(12.03)
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


def test_historical_target_and_stop_advance_can_update_both_orders_together():
    host,a,_=acquired()
    a.state['historical_hod_entry']['desired_stop']=10.1
    # Target selection is restricted to levels with historical ancestry.
    r=host.evaluate(a,candle(3,10.58,position_quantity=100))
    assert [i.action for i in r.evaluation.intents]==['replace_protective_stop','replace_profit_target']
    assert r.state['active_stop']==pytest.approx(10.1)
    assert r.evaluation.intents[-1].profit_target_price==pytest.approx(11.49)
    assert r.evaluation.intents[-1].metadata['profit_target_selection']['triggering_breakout']['lower']==10.4


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
    a.state['historical_hod_entry'].update(target={'price':3.82,'level':levels[1],'trigger_level':levels[0]},
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


def test_failed_resistance_exit_requires_second_consecutive_red_second():
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,10.40,opened=10.39,position_quantity=100),bar_high=10.43))
    a=replace(a,state=r.state,status=r.status)
    o=candle(4,10.38,opened=10.40,position_quantity=100)
    r=host.evaluate(a,o)
    assert r.state['historical_hod_entry']['pending_failed_attempt']
    a=replace(a,state=r.state,status=r.status)
    early=replace(o,source_timeframe='100ms',observed_at=o.observed_at+timedelta(milliseconds=100),price=10.37)
    assert not host.evaluate(a,early).evaluation.intents
    r=host.evaluate(a,candle(5,10.37,opened=10.38,position_quantity=100))
    intent,=r.evaluation.intents
    assert intent.reason=='red_close_below_attempt_open'
    assert intent.quantity==100
    assert intent.metadata['failed_resistance_exit']['candle_confirmation']['consecutive_red_closes']==2


@pytest.mark.parametrize('reset_open,reset_close',[(10.38,10.39),(10.39,10.39)])
def test_rejection_monitor_resets_on_green_or_doji(reset_open,reset_close):
    _,_,o=ready()
    active={'pending_failed_attempt':dict(at_ms=round(o.observed_at.timestamp()*1000),trigger_close=10.38),
        'failed_resistance_exit':{'level':rows()[1]}}
    def bar(ms,opened,close):
        return replace(o,source_timeframe='1s',observed_at=o.observed_at+timedelta(milliseconds=ms),bar_open=opened,price=close)
    assert not H.confirm_failed_attempt(active,bar(1000,reset_open,reset_close))
    assert not H.confirm_failed_attempt(active,bar(2000,10.39,10.38))
    assert not H.confirm_failed_attempt(active,bar(2000,10.39,10.37))
    assert not H.confirm_failed_attempt(active,replace(bar(3000,10.38,10.37),evaluation_events=('market_data_update',)))
    assert H.confirm_failed_attempt(active,replace(bar(3000,10.38,10.37),bar_low=10.36),
        previous_bar=dict(end=o.observed_at.timestamp()+2,open=10.39,low=10.37))


def test_missing_second_breaks_consecutive_rejection_confirmation():
    _,_,o=ready()
    active={'pending_failed_attempt':dict(at_ms=round(o.observed_at.timestamp()*1000),trigger_close=10.38),
        'failed_resistance_exit':{'level':rows()[1]}}
    later=replace(o,source_timeframe='1s',observed_at=o.observed_at+timedelta(seconds=2),bar_open=10.38,price=10.37)
    assert not H.confirm_failed_attempt(active,later)
    assert H.confirm_failed_attempt(active,replace(later,observed_at=later.observed_at+timedelta(seconds=1),bar_low=10.35),
        previous_bar=dict(end=later.observed_at.timestamp(),open=10.38,low=10.36))


def test_sugp_reclaimed_failure_cannot_exit_later_red_sequence():
    _,_,o=ready()
    at=o.observed_at.timestamp()
    active={'pending_failed_attempt':dict(at_ms=round(at*1000),trigger_close=3.8156),
        'failed_resistance_exit':{'level':dict(rows()[0],lower=3.83,upper=3.85)}}
    # Reclaiming even the lower part of the band cancels the old failure.
    recovered=replace(o,observed_at=o.observed_at+timedelta(seconds=1),price=3.84,bar_open=3.82,bar_low=3.82)
    assert not H.confirm_failed_attempt(active,recovered)
    assert 'pending_failed_attempt' not in active
    # Recorded closes at 04:10:53 and 04:10:54 have rising lows and remain above 3.83.
    for seconds,close,low in ((7,3.9714,3.9401),(8,3.98,3.96)):
        red=replace(o,observed_at=o.observed_at+timedelta(seconds=seconds),price=close,bar_open=4.,bar_low=low)
        assert not H.confirm_failed_attempt(active,red,
            previous_bar=dict(end=at+seconds-1,open=4.,low=3.9401))


@pytest.mark.parametrize('close,low,expected',[(10.38,10.36,True),(10.38,10.375,False),(10.40,10.36,False)])
def test_pending_failure_rechecks_floor_and_lower_low(close,low,expected):
    _,_,o=ready()
    active={'pending_failed_attempt':dict(at_ms=round(o.observed_at.timestamp()*1000),trigger_close=10.38),
        'failed_resistance_exit':{'level':rows()[1]}}
    red=replace(o,observed_at=o.observed_at+timedelta(seconds=1),price=close,bar_open=10.41,bar_low=low)
    assert H.confirm_failed_attempt(active,red,
        previous_bar=dict(end=o.observed_at.timestamp(),open=10.41,low=10.37))==expected


def test_red_lower_close_without_a_resistance_attempt_is_not_this_exit():
    host,a,_=acquired()
    r=host.evaluate(a,candle(3,10.2,opened=10.15,position_quantity=100))
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.14,opened=10.2,position_quantity=100))
    assert not any(v.reason=='red_close_below_attempt_open' for v in r.evaluation.intents)


def test_hod_without_historical_ancestry_cannot_arm_rejection_exit():
    active={'level':{'reference_kind':'hod','price':3.57,'lower':3.57,'upper':3.57},
        'confirmed_at':1,'management_base':{'lower':3.57,'tolerance':.01}}
    previous=dict(time=2,end=3,open=3.60,high=3.60,low=3.59,close=3.59)
    bar=dict(time=3,end=4,open=3.58,high=3.58,low=3.55,close=3.5562)
    assert H.management({},active,bar,H.DEFAULTS,.01,previous_bar=previous)!='red_close_below_attempt_open'
    assert 'failed_resistance_exit' not in active
    for changed in (dict(bar,close=3.575),dict(bar,open=3.55),dict(bar,time=4,end=5)):
        assert H.management({},deepcopy(active),changed,H.DEFAULTS,.01,previous_bar=previous)!='red_close_below_attempt_open'


@pytest.mark.parametrize('lower,upper,previous,bar,expected', [
    (3.83,3.85,(3.83,3.89,3.83,3.88),(3.82,3.86,3.81,3.8156),True),
    (3.94,3.9662931,(4.,4.,3.96,3.98),(3.99,4.,3.94,3.95),False),
    (4.09918,4.110822,(4.1,4.12,4.05,4.1),(4.1,4.13,4.0743,4.09),False),
])
def test_sugp_recorded_failed_attempt_closes(lower,upper,previous,bar,expected):
    def completed(values, start):
        return dict(zip(('open','high','low','close'),values),time=NOW.timestamp()+start,end=NOW.timestamp()+start+1)
    active={'confirmed_at':NOW.timestamp()+1,'management_base':{'lower':3.,'tolerance':.01}}
    resistance=dict(side='resistance',lower=lower,upper=upper,confirmed_at=NOW.timestamp()+1,level_id=1,oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
    reason=H.management({},active,completed(bar,3),H.DEFAULTS,.01,
        previous_bar=completed(previous,2),resistance_levels=[resistance])
    assert (reason=='red_close_below_attempt_open') == expected


def test_retest_tracks_frozen_band_until_actual_failure():
    active={'confirmed_at':NOW.timestamp()+1,'management_base':{'lower':3.,'tolerance':.01}}
    level=dict(side='resistance',lower=3.94,upper=3.9662931,confirmed_at=NOW.timestamp()+1,level_id=1,oldest_member_confirmed_at_ms=(NOW.timestamp()-86400)*1000)
    previous=dict(time=NOW.timestamp()+2,end=NOW.timestamp()+3,open=4.,high=4.,low=3.96,close=3.98)
    retest=dict(time=NOW.timestamp()+3,end=NOW.timestamp()+4,open=3.99,high=4.,low=3.94,close=3.95)
    assert H.management({},active,retest,H.DEFAULTS,.01,previous_bar=previous,resistance_levels=[level])==''
    # A changed/absent projection must not rewrite the encountered band's floor.
    changed=dict(level,lower=3.96,upper=3.98)
    holding=dict(time=NOW.timestamp()+4,end=NOW.timestamp()+5,open=3.95,high=3.96,low=3.94,close=3.94)
    assert H.management({},active,holding,H.DEFAULTS,.01,previous_bar=retest,resistance_levels=[changed])==''
    failed=dict(time=NOW.timestamp()+5,end=NOW.timestamp()+6,open=3.94,high=3.95,low=3.92,close=3.93)
    assert H.management({},active,failed,H.DEFAULTS,.01,previous_bar=holding)=='red_close_below_attempt_open'
    assert active['failed_resistance_exit']['level']==level
    assert active['failed_resistance_exit']['attempt_kind']=='failed_breakout'
    gap=dict(failed,time=NOW.timestamp()+6,end=NOW.timestamp()+7)
    assert H.management({},active,gap,H.DEFAULTS,.01,previous_bar=holding)==''


def test_engine_keeps_position_on_red_close_inside_broken_band():
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,10.44,opened=10.43,position_quantity=100),bar_low=10.41))
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.41,opened=10.44,position_quantity=100))
    assert not any(v.action=='exit' for v in r.evaluation.intents)
    assert r.state['historical_hod_entry']['resistance_attempts']['levels']


@pytest.mark.parametrize('offset',[0.,.01])
def test_reentry_uses_prior_body_high_excludes_wicks_and_current_candle(offset):
    host,a,_=acquired()
    a.parameters['historical_hod']['entry_breakout_offset']=offset
    r=host.evaluate(a,replace(candle(3,10.2,position_quantity=100),bar_high=10.29))
    a=replace(a,state=r.state,status=S.AssignmentStatus.WATCHING)
    r=host.evaluate(a,candle(4,10.19));assert not r.evaluation.intents
    a=replace(a,state=r.state,status=r.status)
    assert not host.evaluate(a,candle(5,10.2)).evaluation.intents
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
    assert profile['parameters']['historical_hod']['entry_breakout_offset']==.01
    assert profile['parameters']['historical_hod']['sizing_mode']=='cash_tranches'
    original={m['mandate_id']:m for m in configuration_base()['portfolio']['mandates']}
    for mandate in payload['portfolio']['mandates']:
        if mandate.get('run_plan_id')==plan:
            assert mandate['maximum_planned_risk_fraction']==original[mandate['mandate_id'].removeprefix(plan+'-')]['maximum_planned_risk_fraction']
    assert next(p for p in validated['run_plans']['plans'] if p['run_plan_id']==plan)['allowed_environments']==['backtest']


def test_cash_tranches_add_once_per_higher_breakout_with_shared_protection():
    host,a,o=ready()
    p=deepcopy(a.parameters);p['historical_hod']['sizing_mode']='cash_tranches'
    a=replace(a,parameters=p,permissions=replace(a.permissions,add=True))
    r=host.evaluate(a,o);initial=r.evaluation.intents[0]
    assert initial.capital_request.mode=='mandate_fraction'
    assert initial.capital_request.value==.9
    assert initial.metadata['cash_tranche']['index']==0
    initial_stop=r.state['historical_hod_entry']['initial_stop_selection']
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    red=host.evaluate(a,candle(3,10.44,opened=10.5,position_quantity=1000))
    assert not any(i.action=='add_long' for i in red.evaluation.intents)
    r=host.evaluate(a,candle(3,10.44,position_quantity=1000))
    addition=next(i for i in r.evaluation.intents if i.action=='add_long')
    assert addition.metadata['cash_tranche']['index']==1
    assert addition.invalidation_price==r.state['active_stop']
    assert addition.profit_target_price==r.state['structural_profit_targets'][0]
    assert addition.resolved_execution_policy().envelope.deadline_ms==0
    assert r.state['historical_hod_entry']['initial_stop_selection']==initial_stop
    a=replace(a,state=r.state,status=r.status)
    assert not any(i.action=='add_long' for i in host.evaluate(a,candle(3,10.44,position_quantity=1000)).evaluation.intents)
    r=host.evaluate(a,candle(4,10.58,position_quantity=2000))
    assert next(i for i in r.evaluation.intents if i.action=='add_long').metadata['cash_tranche']['index']==2
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(5,11.,position_quantity=3000))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)


def test_cash_add_confirms_red_break_on_next_non_red_close_without_skipping_level():
    host,a,o=ready()
    p=deepcopy(a.parameters);p['historical_hod']['sizing_mode']='cash_tranches'
    a=replace(a,parameters=p,permissions=replace(a.permissions,add=True))
    r=host.evaluate(a,o);a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,candle(3,10.44,opened=10.45,position_quantity=1000))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(4,10.58,opened=10.44,position_quantity=1000))
    addition=next(i for i in r.evaluation.intents if i.action=='add_long')
    assert addition.metadata['tranche_breakout']['lower']==10.4
    assert addition.metadata['cash_tranche']['index']==1
    # A blocked non-red confirmation must not become a delayed add later.
    blocked=host.evaluate(a,replace(candle(4,10.48,position_quantity=1000),bid=10.,ask=10.5))
    assert not any(i.action=='add_long' for i in blocked.evaluation.intents)
    a=replace(a,state=blocked.state,status=blocked.status)
    r=host.evaluate(a,candle(5,10.49,position_quantity=1000))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)


def test_point_projection_restores_bands_and_missing_lineage_fails_closed():
    o=candle(1,10.01)
    projected=tuple(dict(r,band_lower=r['lower'],band_upper=r['upper'],lower=r['price'],upper=r['price']) for r in rows())
    restored=H.selected_levels(replace(o,structural_resistance_levels=projected),H.DEFAULTS,o.observed_at.timestamp())
    assert restored[0]['lower']==10. and restored[0]['upper']==10.02
    bad=dict(rows()[0]);bad.pop('oldest_member_confirmed_at_ms')
    with pytest.raises(ValueError,match='provenance'):
        H.selected_levels(replace(o,structural_resistance_levels=(bad,)),H.DEFAULTS,o.observed_at.timestamp())


def test_rejection_requires_failed_recovery_and_volume_warning_alone_holds():
    active={'confirmed_at':NOW.timestamp()+1,'management_base':{'lower':9.,'tolerance':.02}}
    bar={'close':10.9,'low':10.85,'high':11.1,'end':NOW.timestamp()+2}
    rejection={'state':'rejection','level':{'side':'resistance','lower':11.,'upper':11.02,'oldest_member_confirmed_at_ms':(NOW.timestamp()-86400)*1000}}
    assert H.management({'global_events':[rejection]},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=NOW.timestamp()+3,close=10.8,low=10.7)
    assert H.management({},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=NOW.timestamp()+4,close=10.85,low=10.8)
    recovery={'state':'lower_high_confirmed','level':{'price':10.9,'pivot_at':NOW.timestamp()+3.5,'confirmed_at':NOW.timestamp()+4}}
    assert H.management({'local_events':[recovery]},active,bar,H.DEFAULTS,.01)==''
    bar.update(end=NOW.timestamp()+5,close=10.68,low=10.68)
    assert H.management({},active,bar,H.DEFAULTS,.01)=='resistance_rejection_failed_recovery'
    assert H.management({'volume_analysis':{'reversal_outcomes':[{'direction':'bearish',
        'outcome':'structural_reversal_confirmation'}]}},
        {'confirmed_at':NOW.timestamp()+1,'management_base':{'lower':10.,'tolerance':.02}},bar,H.DEFAULTS,.01)==''


def test_replay_passive_adapter_observes_both_clocks_before_assignment():
    import asyncio
    from unittest.mock import AsyncMock,patch
    from src.backend.replay_run_service import ReplayRunController
    from tests.test_structural_recovery import BOOK
    _,a,_=ready()
    fake=SimpleNamespace(definition=SimpleNamespace(configuration_revision={'payload':{'strategy':{'parameters':a.parameters}}},
        experimental_structure_book=BOOK['id']),_candle_detector_states={},_structural_market_streams={},
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
    persisted=ReplayRunController._checkpoint_candle_detectors(fake)['TEST']['structural_recovery']
    assert persisted['historical_hod_observation']==stream['historical_hod_observation']
    from src.trading_runtime.structural_recovery import MarketStream
    assert MarketStream(persisted).checkpoint()==persisted
    passive=stream['historical_hod_observation']
    assert passive['macd_at']==(NOW+timedelta(seconds=5)).timestamp()
    assert passive['body_high']==10.1 and passive['hod']==10.3


def test_submitted_acquisition_persists_and_rejected_replacement_retries():
    host,a,o=ready();r=host.evaluate(a,o)
    pending=replace(a,state=r.state,status=r.status)
    expired=host.evaluate(pending,candle(3,10.04))
    assert not expired.evaluation.intents
    envelope=r.evaluation.intents[0].resolved_execution_policy().envelope
    assert envelope.persist_until_cancelled and envelope.maximum_buy_price is None
    stopped=host.evaluate(pending,replace(candle(3,9.97),evaluation_events=('market_data_update',)))
    assert stopped.evaluation.intents[0].action=='exit'
    assert stopped.evaluation.intents[0].metadata['cancel_entry_acquisition']
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


@pytest.mark.parametrize('cash_tranches,add_quote_size',[(False,10000),(True,10000),(True,100)])
def test_runtime_places_broker_stop_and_full_target(tmp_path,cash_tranches,add_quote_size):
    import asyncio
    from tests import test_long_momentum_strategy as T
    from src.trading_runtime.journal import TradingJournal
    async def run():
        _,a,o=ready()
        if cash_tranches:
            p=deepcopy(a.parameters);p['historical_hod']['sizing_mode']='cash_tranches'
            a=replace(a,parameters=p,permissions=replace(a.permissions,add=True))
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
            if cash_tranches:
                quantities=[sum(lot.quantity for lot in runtime.portfolio.allocations.values())]
                for index,price in [(3,10.44),(4,10.58)]:
                    obs=candle(index,price,position_quantity=quantities[-1])
                    stamp=obs.observed_at-timedelta(milliseconds=1)
                    await broker.on_market_event(T.QuoteEvent(ask_exchange=11,ask_price=obs.ask,ask_size=add_quote_size,
                        bid_exchange=12,bid_price=obs.bid,bid_size=10000,conditions=(),indicators=(),
                        ingest_ts=stamp,raw={'conid':123},sequence=index,source='test',tape=3,ticker='TEST',ts=stamp))
                    await runtime.process_strategy_observation(obs)
                    # Acquire the remaining tranche in separate causal quote
                    # updates while earlier tranches retain their protection.
                    for part in range(1,100):
                        entries=[order for order in await broker.live_orders()
                            if order.side=='BUY' and order.order_status not in {'Filled','Cancelled'}]
                        if not entries:
                            break
                        stamp=obs.observed_at+timedelta(milliseconds=part)
                        await runtime.process_event(T.QuoteEvent(ask_exchange=11,ask_price=obs.ask,ask_size=add_quote_size,
                            bid_exchange=12,bid_price=obs.bid,bid_size=10000,conditions=(),indicators=(),
                            ingest_ts=stamp,raw={'conid':123},sequence=index*100+part,source='test',tape=3,ticker='TEST',ts=stamp),
                            evaluate_strategy=False)
                    quantities.append(sum(lot.quantity for lot in runtime.portfolio.allocations.values()))
                    working=[order for order in await broker.live_orders() if order.order_status not in {'Filled','Cancelled','Inactive'}]
                    stops=[order for order in working if order.orderType=='STP']
                    targets=[order for order in working if order.orderType=='LMT' and order.side=='SELL']
                    assert len({order.auxPrice for order in stops})==1
                    assert len({order.price for order in targets})==1
                    assert sum(order.remainingQuantity for order in stops)==quantities[-1]
                    assert sum(order.remainingQuantity for order in targets)==quantities[-1]
                assert quantities[0] < quantities[1] < quantities[2]
                assert len(runtime.portfolio.allocations)==1
                if add_quote_size==100:
                    # One broker match can execute several tranche stops before
                    # OMS consumes each order-state message. Never repair shares
                    # already sold by that same match.
                    bid=min(order.auxPrice for order in stops)-.01
                    for offset,size in ((1,1100),(2,10000)):
                        stamp=obs.observed_at+timedelta(seconds=offset)
                        await runtime.process_event(T.QuoteEvent(ask_exchange=11,ask_price=bid+.01,ask_size=10000,
                            bid_exchange=12,bid_price=bid,bid_size=size,conditions=(),indicators=(),
                            ingest_ts=stamp,raw={'conid':123},sequence=1000+offset,source='test',tape=3,ticker='TEST',ts=stamp),
                            evaluate_strategy=False)
                        held=sum(float(pos.position) for pos in await broker.positions('sim'))
                        working=[order for order in await broker.live_orders()
                            if order.order_status not in {'Filled','Cancelled','Inactive'}]
                        assert sum(order.remainingQuantity for order in working if order.orderType=='STP')==held
                        assert sum(order.remainingQuantity for order in working if order.side=='SELL' and order.orderType=='LMT')==held
                    assert held==0
        finally:
            journal.close()
    asyncio.run(run())


def test_target_excludes_current_day_but_accepts_merged_historical_ancestry():
    historical=rows()[2]
    today=dict(historical,price=10.50,lower=10.49,upper=10.51,
        oldest_member_confirmed_at_ms=NOW.timestamp()*1000,confirmed_at_ms=NOW.timestamp()*1000)
    merged=dict(historical,confirmed_at_ms=NOW.timestamp()*1000)
    broken=dict(price=10.,upper=10.02)
    selected=H.target_selection([today,merged],broken,10.1,H.DEFAULTS,.01,session='2026-08-21')
    assert selected['level']==merged
    assert H.target_selection([today],broken,10.1,H.DEFAULTS,.01,session='2026-08-21') is None
    assert H.target_selection([dict(today,oldest_member_confirmed_at_ms=None)],broken,10.1,H.DEFAULTS,.01,session='2026-08-21') is None


@pytest.mark.parametrize('merged',[False,True])
def test_stop_ratchet_uses_historical_ancestry_not_latest_confirmation(merged):
    host,a,_=acquired()
    levels=list(rows())
    levels[1]=dict(levels[1],confirmed_at_ms=NOW.timestamp()*1000,
        oldest_member_confirmed_at_ms=(NOW.timestamp()-(86400 if merged else 0))*1000)
    a.state['historical_hod_state']['rows']=levels
    for index in (3,4):
        o=replace(candle(index,10.43,position_quantity=100),structural_resistance_levels=tuple(levels))
        r=host.evaluate(a,o);a=replace(a,state=r.state,status=r.status)
    assert (a.state['active_stop']==pytest.approx(10.39))==merged


@pytest.mark.parametrize('merged',[False,True])
def test_juns_current_day_rejection_requires_historical_ancestry(merged):
    previous=dict(time=1787311285.,end=1787311286.,open=7.2906,high=7.47,low=7.23,close=7.39)
    bar=dict(time=1787311286.,end=1787311287.,open=7.3099,high=7.39,low=7.21,close=7.21)
    resistance=dict(side=-1,lower=7.29,upper=7.31,price=7.30,confirmed_at_ms=1787310869000,
        oldest_member_confirmed_at_ms=1787310869000-(86400000 if merged else 0),unified_level_id='juns-level')
    active={'confirmed_at':1787311284.,'management_base':{'lower':6.88,'tolerance':.01}}
    reason=H.management({},active,bar,H.DEFAULTS,.01,previous_bar=previous,resistance_levels=[resistance])
    assert (reason=='red_close_below_attempt_open')==merged


def test_restored_current_day_rejection_cannot_confirm_exit():
    _,_,o=ready()
    active={'pending_failed_attempt':{'at_ms':round(o.observed_at.timestamp()*1000),'trigger_close':10.38},
        'failed_resistance_exit':{'level':dict(rows()[0],oldest_member_confirmed_at_ms=NOW.timestamp()*1000)}}
    red=replace(o,observed_at=o.observed_at+timedelta(seconds=1),bar_open=10.38,price=10.37)
    assert not H.confirm_failed_attempt(active,red)
    assert 'pending_failed_attempt' not in active


@pytest.mark.parametrize('interrupt',[None,'green','flat','gap','new_projection'])
def test_red_sequence_before_historical_failure_is_not_restarted(interrupt):
    host,a,_=acquired()
    # Three red closes remain in the historical 10.40--10.42 band.
    for index,close in ((3,10.418),(4,10.414),(5,10.410)):
        opened=close+.004
        if index==5 and interrupt in ('green','flat'):
            opened=close-.004 if interrupt=='green' else close
        r=host.evaluate(a,candle(index,close,opened=opened,position_quantity=100))
        assert not any(i.action=='exit' for i in r.evaluation.intents)
        a=replace(a,state=r.state,status=r.status)
    if interrupt=='new_projection':
        a.state['historical_hod_state']['rows']=[r for r in rows() if r['lower']!=10.4]
        a.state['historical_hod_entry']['resistance_attempts']={}
    index=7 if interrupt=='gap' else 6
    r=host.evaluate(a,candle(index,10.38,opened=10.41,position_quantity=100))
    exits=[i for i in r.evaluation.intents if i.action=='exit']
    if interrupt in ('green','flat','gap'):
        assert not exits
    else:
        assert len(exits)==1 and exits[0].reason=='red_close_below_attempt_open'
        assert exits[0].metadata['failed_resistance_exit']['candle_confirmation']['consecutive_red_closes']==4


def test_initial_target_uses_first_historical_above_entry_and_frozen_trigger():
    host,a,o=ready()
    today=dict(level(-1,10.2,10.22),oldest_member_confirmed_at_ms=NOW.timestamp()*1000,confirmed_at_ms=NOW.timestamp()*1000)
    market_rows=(*rows(),today)
    r=host.evaluate(a,replace(o,structural_resistance_levels=market_rows))
    target=r.state['historical_hod_entry']['target']
    assert target['trigger_level']['lower']==10.4
    assert target['reference']==pytest.approx(10.41*1.05)
    assert target['level']['lower']==10.95
    a=replace(a,state=r.state,status=S.AssignmentStatus.MANAGING)
    r=host.evaluate(a,replace(candle(3,10.23,position_quantity=100),structural_resistance_levels=market_rows))
    assert not any(i.action=='replace_profit_target' for i in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    # The anchor must still work if it disappears from the current projection.
    remaining=tuple(l for l in rows() if l['lower']!=10.4)
    a.state['historical_hod_state']['rows']=remaining
    r=host.evaluate(a,replace(candle(4,10.43,position_quantity=100),structural_resistance_levels=remaining))
    promoted=next(i for i in r.evaluation.intents if i.action=='replace_profit_target')
    selection=promoted.metadata['profit_target_selection']
    assert selection['triggering_breakout']['lower']==10.4
    assert selection['trigger_level']['lower']==10.55
    assert selection['reference']==pytest.approx(10.56*1.05)
    assert selection['level']['lower']==10.95


@pytest.mark.parametrize('next_available', [True,False])
def test_multilevel_close_rebases_above_close_not_old_target(next_available):
    host,a,_=acquired()
    # A current-day level above the close must not displace the historical base.
    today=dict(level(-1,11.1,11.12),oldest_member_confirmed_at_ms=NOW.timestamp()*1000,
        confirmed_at_ms=NOW.timestamp()*1000)
    levels=(*rows(),today) if next_available else tuple(r for r in rows() if r['upper']<11.)
    r=host.evaluate(a,replace(candle(3,11.,position_quantity=100),structural_resistance_levels=levels))
    targets=[v for v in r.evaluation.intents if v.action=='replace_profit_target']
    if not next_available:
        assert not targets
        assert r.state['structural_profit_targets']==[10.94]
        return
    target,=targets
    selection=target.metadata['profit_target_selection']
    assert selection['trigger_level']['lower']==11.5
    assert selection['reference']==pytest.approx(11.51*1.05)
    assert selection['level']['lower']==12.
    assert target.profit_target_price==pytest.approx(12.03)
