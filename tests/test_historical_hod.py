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
    return replace(o,bar_volume=1000,execution_vwap=9.8,macd_line=.01,macd_signal=0.,
        structural_session_high=10.3,structural_support_levels=(),structural_resistance_levels=rows(),**kw)


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
    assert intent.invalidation_price==pytest.approx(9.99)
    assert intent.profit_target_price==pytest.approx(10.54)
    assert len(intent.protection_profile.slices)==1
    assert intent.metadata['mandatory_broker_target']
    assert intent.resolved_execution_policy().envelope.deadline_ms==1000
    assert S.strategy_rule_timeframes(a.parameters)=={'1s','5s'}
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


def test_fresh_cross_vwap_liquidity_and_stale_macd():
    host,a,o=ready()
    for changed in (replace(o,execution_vwap=o.price),replace(o,source_values={}),
                    candle(6,10.04),replace(o,bar_volume=0)):
        assert not host.evaluate(a,changed).evaluation.intents
    state=deepcopy(a.state);state['historical_hod_state']['close']=10.03
    assert not host.evaluate(replace(a,state=state),o).evaluation.intents
    no_target=deepcopy(a.state);no_target['historical_hod_state']['rows']=[rows()[0]]
    assert host.evaluate(replace(a,state=no_target),o).evaluation.signals[0].reason=='qualified_target_unavailable'


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
            assert r.state['active_stop']==pytest.approx(9.99)
        if i==3:
            intent,=r.evaluation.intents
            assert intent.action=='replace_profit_target'
            assert intent.profit_target_price==pytest.approx(10.94)
            assert intent.metadata['profit_target_selection']['reference']==pytest.approx(10.41*1.05)
        a=replace(a,state=r.state,status=r.status)
    assert r.evaluation.intents[0].action=='replace_protective_stop'
    assert r.state['active_stop']==pytest.approx(10.39)
    assert r.evaluation.intents[0].metadata['previous_stop']==pytest.approx(9.99)
    assert r.state['structural_profit_targets']==pytest.approx([10.94])


def test_target_does_not_skip_nearest_resistance_to_force_an_advance():
    # The mathematically closest level may still be the old target's level.
    selected=H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,minimum_target=10.94)
    assert selected is None
    assert H.target_selection(rows(),rows()[1],10.43,H.DEFAULTS,.01,minimum_target=11.49) is None


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
    assert r.evaluation.intents[-1].profit_target_price==pytest.approx(10.94)
    assert r.evaluation.intents[-1].metadata['profit_target_selection']['broken_level']['lower']==10.55


def test_red_breakout_advances_target_on_later_non_red_holding_close():
    host,a,_=acquired()
    for i,price,opened in ((3,10.43,10.44),(4,10.44,10.45)):
        r=host.evaluate(a,candle(i,price,opened=opened,position_quantity=100))
        assert not any(v.action=='replace_profit_target' for v in r.evaluation.intents)
        a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,candle(5,10.45,opened=10.44,position_quantity=100))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(10.94)
    assert target.metadata['profit_target_selection']['broken_level']['upper']==10.42
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
        for low,high in ((3.68,3.71),(3.83,3.85),(3.89,3.91)))
    # Recorded SUGP prices; the position already has a $3.82 target.
    a.state.update(active_stop=3.61,structural_profit_targets=[3.82])
    a.state['historical_hod_entry'].update(stop=3.61,initial_risk=.04,best_close=3.6995,
        management_base={'lower':3.61,'tolerance':.01})
    a.state['historical_hod_state'].update(close=3.6995,rows=levels)
    r=host.evaluate(a,replace(candle(3,3.72,opened=3.7293,position_quantity=100),
        structural_resistance_levels=levels))
    assert not any(v.action=='replace_profit_target' for v in r.evaluation.intents)
    a=replace(a,state=r.state,status=r.status)
    r=host.evaluate(a,replace(candle(4,3.75,opened=3.7007,position_quantity=100),
        structural_resistance_levels=levels))
    target=next(v for v in r.evaluation.intents if v.action=='replace_profit_target')
    assert target.profit_target_price==pytest.approx(3.88)


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
    assert r.state['active_stop']==pytest.approx(9.99)


def test_trailing_uses_frozen_actual_entry_risk_when_historical_unavailable():
    host,a,_=acquired()
    o=replace(candle(3,10.2,position_quantity=100,average_price=10.05),structural_resistance_levels=rows()[2:])
    r=host.evaluate(a,o)
    assert r.state['historical_hod_entry']['initial_risk']==pytest.approx(.06)
    assert r.state['active_stop']==pytest.approx(10.14)


def test_swing_tolerance_is_management_only_and_requires_two_closes():
    active={'confirmed_at':1,'management_base':{'lower':10.,'tolerance':.02}}
    row={'local_events':[],'qualification':{'atr':.2}}
    bar={'close':9.99,'low':9.97,'high':10.1,'end':2}
    assert H.management(row,active,bar,H.DEFAULTS,.01)==''
    bar['close']=9.97
    assert H.management(row,active,bar,H.DEFAULTS,.01)==''
    assert H.management(row,active,bar,H.DEFAULTS,.01)=='protective_swing_failed'
    host,a,_=acquired()
    r=host.evaluate(a,replace(candle(3,9.985,position_quantity=100),source_values={}))
    assert r.evaluation.intents[0].reason=='protective_stop'


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
