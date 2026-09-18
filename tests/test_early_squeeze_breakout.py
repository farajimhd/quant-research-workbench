from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from src.trading_runtime import early_squeeze_breakout as E, strategy_engine as S
from src.trading_runtime.structural_recovery import CONTRACT as DATA, DEFAULTS
from tests.test_vwap_resistance_ladder import fixture as base_fixture, advance, NOW
from tests.test_r1_ladder_candidate import source


def fixture():
    host, a, base = base_fixture()
    p = source()
    p['protection_profile_catalog'] = deepcopy(a.parameters['protection_profile_catalog'])
    p.pop('historical_hod')
    p.update(early_squeeze_breakout_contract=E.CONTRACT, structural_recovery_contract=DATA,
             structural_recovery=dict(DEFAULTS), reentry=dict(enabled=True,after_protective_exit=True))
    a = replace(a, parameters=p, state={})
    def obs(i=0, price=10.39, position=0., open_price=None, high=None, low=None, signal=True):
        o = base(i, price, position, bearish=True)
        sv = {k:v for k,v in o.source_values.items() if 'macd' not in k}
        at = o.observed_at.isoformat()
        for key, value in [('market.volume',100000.),('market.session_dollar_volume',1000000.),
                           ('market.trade_rate_10s',10.),('market.trade_rate_60s',10.),
                           ('indicator.vwap.execution_value@1s',9.)]:
            sv[key] = dict(value=value, observed_at=at)
        if signal:
            sv[E.SIGNAL] = dict(value=True, observed_at=(NOW-timedelta(seconds=5)).isoformat(), event_id='first')
        return replace(o, source_values=sv, source_timeframe='1s', bar_open=open_price if open_price is not None else price-.05,
            bar_high=high if high is not None else price+.01, bar_low=low if low is not None else price-.1,
            bar_volume=1000., average_price=10.43 if position else 0.,
            structural_detector_state=dict(row=dict(effective_at=o.observed_at.timestamp(), candle=dict(volume=1000.))))
    return host, a, obs


def entered():
    host,a,obs=fixture()
    a=advance(a,host.evaluate(a,obs()))
    r=host.evaluate(a,obs(1,10.42))  # midpoint 10.41, equality to upper edge is allowed
    assert r.evaluation.intents[0].action == 'enter_long'
    a=advance(a,r)
    a.state['squeeze_entry'].update(slice_notional=3000.,first_fill_at=NOW.timestamp()+1)
    return host,replace(a,status=S.AssignmentStatus.MANAGING),obs


def test_midpoint_entry_without_macd_and_cash_thirds():
    host,a,obs=fixture();a=advance(a,host.evaluate(a,obs()))
    r=host.evaluate(a,obs(1,10.42));i=r.evaluation.intents[0]
    assert i.invalidation_price == pytest.approx(10.39)
    assert i.profit_target_price == pytest.approx(11.01)
    assert i.capital_request.value == pytest.approx(1/3)
    assert S.strategy_rule_timeframes(a.parameters)=={'100ms','1s'}
    assert i.metadata['activation']['activation_event_id']=='first'


@pytest.mark.parametrize('case', ['fresh', 'missing', 'stale', 'future', 'below', 'invalid', 'wrong_timeframe'])
def test_v2_uses_runtime_one_second_vwap_with_its_own_timestamp(case):
    host,a,obs=fixture()
    a=advance(a,host.evaluate(a,obs()))
    o=obs(1,10.42)
    sv=dict(o.source_values)
    sv.pop('indicator.vwap.execution_value@1s')
    # Same producer called by ReplayRunController for every derived frame.
    sv.update({k:v for k,v in S.strategy_observation_source_values(o,'1s').items()
               if k.startswith('indicator.vwap.')})
    key='indicator.vwap.execution_value@1s'
    if case=='missing':sv.pop(key)
    if case=='stale':sv[key]['observed_at']=(o.observed_at-timedelta(seconds=3)).isoformat()
    if case=='future':sv[key]['observed_at']=(o.observed_at+timedelta(seconds=1)).isoformat()
    if case=='below':sv[key]['value']=11.
    if case=='invalid':sv[key]['value']=float('nan')
    if case=='wrong_timeframe':sv['indicator.vwap.execution_value@100ms']=sv.pop(key)
    # A fresh alias must not hide missing/stale completed-second evidence.
    sv['indicator.vwap.execution_value']=dict(value=9.,observed_at=o.observed_at.isoformat())
    r=host.evaluate(a,replace(o,source_values=sv))
    assert bool(r.evaluation.intents)==(case=='fresh')
    if case!='fresh':
        assert r.evaluation.signals[0].reason=='fresh_price_above_vwap_required'


def test_v1_keeps_its_original_vwap_lookup():
    host,a,obs=fixture()
    a=replace(a,parameters={**a.parameters,'early_squeeze_breakout_contract':E.LEGACY_CONTRACT})
    a=advance(a,host.evaluate(a,obs()))
    r=host.evaluate(a,obs(1,10.42))
    assert not r.evaluation.intents
    assert r.evaluation.signals[0].metadata['contract']==E.LEGACY_CONTRACT


@pytest.mark.parametrize('count,ordinal,price',[(0,3,11.01),(3,3,11.01),(4,2,10.81),(5,2,10.81),(6,1,10.61),(10,1,10.61)])
def test_target_table_uses_selected_overhead_midpoint(count,ordinal,price):
    host,a,obs=fixture();a=advance(a,host.evaluate(a,obs()))
    a.state['squeeze_breakout']['broken']=[f'prior-{i}' for i in range(count)]
    result=host.evaluate(a,obs(1,10.42));intent=result.evaluation.intents[0]
    selection=intent.metadata['profit_target_selection']
    assert selection['ordinal']==ordinal
    assert intent.profit_target_price==pytest.approx(price)
    assert selection['level']['lower'] < price < selection['level']['upper']


def test_midpoint_target_ignores_stale_roles_and_band_whose_midpoint_is_below_ask():
    def level(key,low,high):return dict(unified_level_id=key,lower=low,upper=high)
    inside=level('inside',7.2,7.6);over=level('over',7.7,7.9);stale=level('stale',7.5,7.7)
    market=dict(levels={r['unified_level_id']:r for r in (inside,over,stale)},
                resistance={r['unified_level_id']:r for r in (inside,over)},broken=['over'])
    assert E.overhead_levels(market,7.5,.01,E.CONTRACT)==[over]
    assert E.target_price(over,.01,E.CONTRACT)==pytest.approx(7.8)


def test_v3_preserves_above_band_target():
    host,a,obs=fixture()
    a=replace(a,parameters={**a.parameters,'early_squeeze_breakout_contract':E.RECOVERY_CONTRACT})
    a=advance(a,host.evaluate(a,obs()))
    assert host.evaluate(a,obs(1,10.42)).evaluation.intents[0].profit_target_price==pytest.approx(11.03)


@pytest.mark.parametrize('failure',['no_signal','future_signal','previous_day','red','weak','unfiltered','stale_quote','below_vwap'])
def test_entry_gates_fail_closed(failure):
    host,a,obs=fixture()
    first=obs(signal=failure not in ('no_signal','future_signal','previous_day'))
    a=advance(a,host.evaluate(a,first));o=obs(1,10.42,signal=failure!='no_signal')
    if failure=='future_signal':o.source_values[E.SIGNAL]['observed_at']=(NOW+timedelta(seconds=2)).isoformat()
    if failure=='previous_day':o.source_values[E.SIGNAL]['observed_at']=(NOW-timedelta(days=1)).isoformat()
    if failure=='red':o=replace(o,bar_open=10.43,bar_high=10.44)
    if failure=='weak':o=replace(o,bar_high=10.6)
    if failure=='unfiltered':o=replace(o,structural_resistance_levels=tuple(dict(r,seed_input_policy='legacy') for r in o.structural_resistance_levels))
    if failure=='stale_quote':o.source_values['market.spread_bps']['observed_at']=(NOW-timedelta(seconds=5)).isoformat()
    if failure=='below_vwap':o.source_values['indicator.vwap.execution_value@1s']['value']=11.
    assert not host.evaluate(a,o).evaluation.intents


def test_activation_does_not_move_to_later_occurrence_or_allow_straddling_bar():
    host,a,obs=fixture();first=obs()
    first.source_values[E.SIGNAL]['observed_at']=(NOW+timedelta(milliseconds=500)).isoformat()
    a=advance(a,host.evaluate(a,first));o=obs(1,10.42)
    o.source_values[E.SIGNAL]['observed_at']=(NOW+timedelta(milliseconds=500)).isoformat()
    r=host.evaluate(a,o);assert not r.evaluation.intents
    assert r.evaluation_payload['reason']=='waiting_for_post_squeeze_candle'
    a=advance(a,r);o=obs(2,10.4)
    o.source_values[E.SIGNAL]['observed_at']=(NOW+timedelta(seconds=2)).isoformat()
    r=host.evaluate(a,o)
    assert r.state['squeeze_breakout']['activated_at']==NOW.timestamp()+.5


def test_realtime_fixed_distance_does_not_rebase_on_add_average_or_lower_stop():
    host,a,obs=entered()
    a=advance(a,host.evaluate(a,obs(2,10.44,100)))
    distance=a.state['squeeze_entry']['trail_distance']
    o=replace(obs(2.2,10.55,100),source_timeframe='',evaluation_events=('market_data_update',))
    r=host.evaluate(a,o)
    assert [i.action for i in r.evaluation.intents]==['replace_protective_stop']
    assert r.state['active_stop']==pytest.approx(o.bid-distance)
    a=advance(a,r)
    r=host.evaluate(a,replace(obs(2.3,10.54,200),average_price=10.5,source_timeframe='',evaluation_events=('market_data_update',)))
    assert r.state['squeeze_entry']['trail_distance']==distance
    assert r.state['active_stop']==a.state['active_stop']


def test_every_green_resistance_break_adds_even_weak_close_and_bearish_macd():
    host,a,obs=entered()
    # Move target out for the lifecycle test, as a broker target would otherwise fill.
    a.state['structural_profit_targets']=[20.]
    for i,price in enumerate((10.63,10.83,11.03,11.23),2):
        r=host.evaluate(a,obs(i,price,100,high=price+.4))
        adds=[x for x in r.evaluation.intents if x.action=='add_long']
        assert len(adds)==1 and adds[0].capital_request.value==3000.
        a=advance(a,r)
    assert len(a.state['squeeze_entry']['added_levels'])==4
    r=host.evaluate(a,obs(6,11.24,100))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)


def test_stopout_freezes_close_high_and_reentry_falls_back_to_last_open():
    host,a,obs=entered()
    a=advance(a,host.evaluate(a,obs(2,10.5,100)))
    E.record_exit(a.state,NOW+timedelta(seconds=2.5),'protective_stop',0.)
    frozen=a.state['squeeze_breakout']['recovery']['high']
    a=replace(a,status=S.AssignmentStatus.WATCHING)
    spike=replace(obs(3,10.7),source_timeframe='',evaluation_events=('market_data_update',))
    a=advance(a,host.evaluate(a,spike))
    assert a.state['squeeze_breakout']['recovery']['high']==frozen
    red=obs(4,10.6,open_price=10.65,high=10.7)
    a=advance(a,host.evaluate(a,red));assert a.state['squeeze_breakout']['recovery']['high']==frozen
    o=obs(5,10.61,open_price=10.605,high=10.9)
    r=host.evaluate(a,o);i=r.evaluation.intents[0]
    assert i.metadata['stop_source']=='last_completed_candle_open_offset'
    assert i.invalidation_price==pytest.approx(10.59)  # open above bid is offset immediately
    assert i.metadata['frozen_reentry_high']==frozen


def test_partial_exit_or_target_does_not_authorize_stop_recovery():
    _,a,_=entered()
    E.record_exit(a.state,NOW,'protective_stop',10.)
    assert 'recovery' not in a.state['squeeze_breakout']
    # A stop which starts liquidation owns the frozen reference through final fill.
    frozen=a.state['squeeze_entry']['peak_close']
    a.state['squeeze_entry']['peak_close']=20.
    E.record_exit(a.state,NOW,'managed_exit',0.)
    assert a.state['squeeze_breakout']['recovery']['high']==frozen
    _,a,_=entered();E.record_exit(a.state,NOW,'profit_target',0.)
    assert 'recovery' not in a.state['squeeze_breakout']


def test_unrelated_legacy_settings_do_not_change_decision():
    host,a,obs=fixture();a=advance(a,host.evaluate(a,obs()))
    expected=host.evaluate(a,obs(1,10.42))
    p=deepcopy(a.parameters)
    p.update(require_open_macd_for_entry=True,require_positive_macd_signal_for_entry=True)
    p['momentum_management']={'macd_backstop':{'enabled':True}}
    p['profit_pocket']={'enabled':True}
    actual=host.evaluate(replace(a,parameters=p),obs(1,10.42))
    left,right=actual.evaluation.intents[0],expected.evaluation.intents[0]
    assert (left.action,left.invalidation_price,left.profit_target_price,left.capital_request,left.protection_profile)==(
        right.action,right.invalidation_price,right.profit_target_price,right.capital_request,right.protection_profile)


def test_historical_context_preserves_session_counts_but_cannot_activate():
    host,a,obs=fixture()
    context={}
    for i,price in [(-3,10.1),(-2,10.3),(-1,10.39)]:
        context=E.observe_context(obs(i,price,signal=False),context)
    context=E.observe_context(obs(0,10.42,signal=False),context)
    o=obs(0,10.42,signal=False)
    o=replace(o,structural_detector_state={**o.structural_detector_state,'early_squeeze_context':context})
    r=host.evaluate(a,o)
    assert not r.evaluation.intents
    assert r.state['squeeze_breakout']['broken']==['R1']
    assert 'activated_at' not in r.state['squeeze_breakout']


def test_fill_callback_creates_frozen_reentry_and_capital_callback_funds_own_slice():
    import asyncio
    from types import SimpleNamespace
    host,a,obs=fixture();a=advance(a,host.evaluate(a,obs()))
    result=host.evaluate(a,obs(1,10.42));a=advance(a,result)
    adapter=S.AssignedLongMomentumStrategy([a],revision=47)
    intent=result.evaluation.intents[0]
    adapter.on_capital_request_funded(replace(intent,metadata={**intent.metadata,'unreserved_slice_notional':3000.}))
    key=(a.account_id,a.ticker.upper())
    assert adapter._assignments[key].state['squeeze_entry']['slice_notional']==3000.
    snapshot=SimpleNamespace(assignment_id=a.assignment_id,state='filled',action='enter_long',
        fill_incremental_quantity=100.,filled_quantity=100.,updated_at=NOW+timedelta(seconds=1),
        intent_id=intent.intent_id)
    asyncio.run(adapter.on_order_group_update(snapshot,aggregate_position_quantity=100.))
    held=adapter._assignments[key]
    assert held.state['squeeze_entry']['first_fill_at']==snapshot.updated_at.timestamp()
    snapshot=SimpleNamespace(assignment_id=a.assignment_id,state='filled',action='exit',
        fill_incremental_quantity=100.,filled_quantity=100.,updated_at=NOW+timedelta(seconds=2),
        fill_role='protective_stop',intent_id='stop',reentry_after_fill=True)
    asyncio.run(adapter.on_order_group_update(snapshot,aggregate_position_quantity=0.))
    updated=adapter._assignments[key]
    assert updated.state['squeeze_breakout']['recovery']['high']==10.42
    assert updated.status==S.AssignmentStatus.REENTRY_COOLDOWN
    # Two protective children can each report a flat aggregate in the same
    # broker event. The second fill must not erase the completed stop-out.
    asyncio.run(adapter.on_order_group_update(snapshot,aggregate_position_quantity=0.))
    again=adapter._assignments[key]
    assert again.state['squeeze_breakout']['recovery']==updated.state['squeeze_breakout']['recovery']
    reentry=host.evaluate(again,obs(3,10.6))
    assert reentry.evaluation.intents[0].metadata['reason_code']=='stopout_close_high_reentry'


@pytest.mark.parametrize('spread,allowed',[(249.,True),(251.,False)])
def test_two_point_five_percent_spread_boundary(spread,allowed):
    host,a,obs=fixture()
    a.parameters['liquidity_admission'].update(maximum_current_spread_bps=250.,
        maximum_admission_spread_bps=250.,maximum_spread_bps=250.)
    a=advance(a,host.evaluate(a,obs()))
    o=obs(1,10.42);half=o.price*spread/20000
    r=host.evaluate(a,replace(o,bid=o.price-half,ask=o.price+half))
    assert bool(r.evaluation.intents)==allowed


def test_duplicate_flat_fills_do_not_erase_recovery_during_unfilled_retry():
    _,a,_=entered()
    E.record_exit(a.state,NOW,'protective_stop',0.)
    frozen=deepcopy(a.state['squeeze_breakout']['recovery'])
    a.state['squeeze_entry']={'requested_at':NOW.timestamp()+1}
    E.record_exit(a.state,NOW,'protective_stop',0.)
    assert a.state['squeeze_breakout']['recovery']==frozen


def test_v2_keeps_historical_flat_callback_behavior():
    _,a,_=entered()
    E.record_exit(a.state,NOW,'protective_stop',0.,contract=E.VWAP_CONTRACT)
    E.record_exit(a.state,NOW,'protective_stop',0.,contract=E.VWAP_CONTRACT)
    assert 'recovery' not in a.state['squeeze_breakout']


def test_reentry_offsets_confirmed_swing_anchor_above_bid_without_discarding_it():
    host,a,obs=entered()
    E.record_exit(a.state,NOW+timedelta(seconds=2),'protective_stop',0.)
    a=replace(a,status=S.AssignmentStatus.REENTRY_COOLDOWN)
    o=obs(4,10.8,open_price=10.75)
    swing=dict(side=1,state='active',lower=10.9,price=10.91,upper=10.92,
               pivot_at=NOW.timestamp()+2,confirmed_at=NOW.timestamp()+3)
    o=replace(o,structural_detector_state={'row':{**o.structural_detector_state['row'],'local_swings':[swing]}})
    result=host.evaluate(a,o)
    intent=result.evaluation.intents[0]
    assert intent.metadata['stop_source']=='confirmed_swing_above_resistance'
    assert intent.invalidation_price==pytest.approx(E.below(swing['lower'],o.bid,.01))


def test_rejected_add_is_not_consumed_and_retry_has_no_macd_or_top_quarter_gate():
    import asyncio
    host,a,obs=entered();a.state['structural_profit_targets']=[20.]
    r=host.evaluate(a,obs(2,10.63,100,high=11.));a=advance(a,r)
    add=next(i for i in r.evaluation.intents if i.action=='add_long')
    adapter=S.AssignedLongMomentumStrategy([a],revision=47)
    asyncio.run(adapter.on_intent_rejected(add,reasons=('cash',),event_time=NOW+timedelta(seconds=2)))
    a=adapter._assignments[(a.account_id,a.ticker.upper())]
    r=host.evaluate(a,obs(3,10.64,100,high=11.))
    assert any(i.action=='add_long' for i in r.evaluation.intents)


def test_target_replacement_rejection_retries_without_new_break():
    import asyncio
    host,a,obs=entered()
    r=host.evaluate(a,obs(2,10.63,100));a=advance(a,r)
    target=next(i for i in r.evaluation.intents if i.action=='replace_profit_target')
    adapter=S.AssignedLongMomentumStrategy([a],revision=47)
    asyncio.run(adapter.on_intent_rejected(target,reasons=('replacement',),event_time=NOW+timedelta(seconds=2)))
    a=adapter.assignments()[0]
    r=host.evaluate(a,obs(3,10.64,100))
    assert any(i.action=='replace_profit_target' and i.profit_target_price==target.profit_target_price for i in r.evaluation.intents)


def test_competing_contract_fails_instead_of_leaking_behavior():
    host,a,obs=fixture();a.parameters['vwap_ladder_contract']='vwap-midpoint-resistance-ladder-v1'
    with pytest.raises(ValueError,match='cannot compose'):
        host.evaluate(a,obs())


def test_actual_simulated_broker_bracket_uses_only_explicit_stop_and_full_target(tmp_path):
    import asyncio
    from tests import test_order_management as helpers
    from src.trading_runtime.domain import InstrumentContract, TradingMode
    from src.trading_runtime.order_management import BrokerCommunicationPolicy
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
    from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
    from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES
    async def run():
        broker=SimulatedBrokerAdapter(['DU1'],mode=TradingMode.BACKTEST)
        manager,journal=await helpers.OrderManagementPolicyTests()._manager(str(tmp_path),broker,
            policy=BrokerCommunicationPolicy(),causal_execution_clock=True)
        planner=IbkrStrategyOrderPlanner()
        manager.planner=lambda intent,account,event: planner.plan(account_id=account,
            instrument=InstrumentContract('TEST',123,'TEST','STK','USD'),intent=intent,
            strategy_id='strategy-1',strategy_revision=47)
        host,a,obs=fixture();a=advance(a,host.evaluate(a,obs()))
        new=host.evaluate(a,obs(1,10.42)).evaluation.intents[0]
        request=replace(new,ticker='TEST',event_time=helpers.NOW,quantity=100.,capital_request=None,
            metadata={**new.metadata,'bid':10.41,'ask':10.43,'quote_observed_at':helpers.NOW.isoformat()})
        try:
            snap=await manager.submit_intent(helpers.portfolio_approved(journal,request),account_id='DU1',event=None)
            group=manager._groups[snap.group_id]
            root=next(k for k,v in group.broker_order_roles.items() if v=='entry')
            broker._apply_fill(broker._orders[root],helpers.NOW,10.43,100.)
            order=next(x for x in await broker.live_orders() if str(x.orderId)==root)
            await manager.on_order_update(order)
            orders=[x for x in await broker.live_orders() if x.order_status in OPEN_ORDER_STATUSES]
            stops=[x for x in orders if group.broker_order_roles.get(str(x.orderId))=='protective_stop']
            targets=[x for x in orders if group.broker_order_roles.get(str(x.orderId))=='profit_target']
            assert stops and all(x.auxPrice==pytest.approx(10.39) for x in stops)
            assert targets and all(x.price==pytest.approx(11.01) for x in targets)
            assert all(x.totalSize==100 for x in stops+targets)
        finally:
            await manager.close();journal.close()
    asyncio.run(run())
