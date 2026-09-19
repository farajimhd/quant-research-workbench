from copy import deepcopy
from dataclasses import replace
import pytest
from src.trading_runtime import early_squeeze_price as P, early_squeeze_breakout as E, strategy_engine as S
from tests.test_early_squeeze_price import fixture as original_fixture
from tests.test_vwap_resistance_ladder import advance


def fixture():
    h,a,t=original_fixture()
    return h,replace(a,parameters={**a.parameters,'early_squeeze_breakout_contract':P.STRICT_CONTRACT}),t


def opened():
    h,a,t=fixture();a=advance(a,h.evaluate(a,t()))
    a=advance(a,h.evaluate(a,t(.01,10.44)))
    a.state['squeeze_entry'].update(first_fill_at=t().observed_at.timestamp(),slice_notional=3000.)
    return h,replace(a,status=S.AssignmentStatus.MANAGING),t


def test_no_legacy_recovery_even_when_frozen_high_crosses():
    h,a,t=fixture();a=advance(a,h.evaluate(a,t(0.,10.55)))
    a.state['squeeze_breakout']['recovery']=dict(high=10.59,stopped_at=0,breakout_at=0,anchor={})
    assert not h.evaluate(a,t(.01,10.6)).evaluation.intents


def test_stop_trails_trade_high_even_when_bid_is_lower():
    h,a,t=opened()
    a.state['squeeze_entry'].update(trail_distance=.07,peak_price=10.44,structural_stop=10.37)
    a.state['active_stop']=10.37
    o=replace(t(.02,10.56,100.),bid=10.50,ask=10.57)
    r=h.evaluate(a,o)
    i=next(i for i in r.evaluation.intents if i.action=='replace_protective_stop')
    assert i.invalidation_price==pytest.approx(10.49)
    assert i.metadata['stop_trigger_source']=='eligible_trade'
    a=advance(a,r)
    q=replace(t(.03,10.56,100.),bid=10.47,ask=10.57,source_signal_ids=('quote:test',))
    assert not any(i.action=='exit' for i in h.evaluate(a,q).evaluation.intents)
    assert any(i.action=='exit' for i in h.evaluate(a,replace(t(.04,10.48,100.),bid=10.47)).evaluation.intents)


def test_same_level_requires_new_high_after_exit_and_cannot_add_entry_level():
    h,a,t=opened()
    r=h.evaluate(a,t(.02,10.46,100.));a=advance(a,r)
    assert not any(i.action=='add_long' for i in r.evaluation.intents)
    E.record_exit(a.state,t(.03).observed_at,'protective_stop',0,contract=P.STRICT_CONTRACT)
    assert 'recovery' not in a.state['squeeze_breakout']
    a=replace(a,status=S.AssignmentStatus.WATCHING)
    a=advance(a,h.evaluate(a,t(.04,10.42)))
    assert not h.evaluate(a,t(.05,10.44)).evaluation.intents
    r=h.evaluate(a,t(.06,10.47))
    assert any(i.action=='enter_long' for i in r.evaluation.intents)


def test_rejected_addition_stays_consumed_and_protection_uses_new_band():
    h,a,t=opened()
    r=h.evaluate(a,t(.02,10.84,100.));a=advance(a,r)
    i=next(i for i in r.evaluation.intents if i.action=='add_long')
    assert i.invalidation_price>=10.79
    keys=i.metadata['squeeze_add_levels'];E.release_add(a.state,keys)
    assert not a.state['squeeze_entry']['pending_adds']
    a=advance(a,h.evaluate(a,t(.03,10.81,100.)))
    r=h.evaluate(a,t(.04,10.85,100.))
    assert not any(i.action=='add_long' for i in r.evaluation.intents)


def test_pending_gray_addition_is_removed():
    h,a,t=opened();o=t(.02,10.84,100.)
    gray=dict(o.structural_resistance_levels[1],role='transition',transition_from='resistance')
    a.state['squeeze_entry']['pending_adds']={gray['unified_level_id']:gray}
    o=replace(o,structural_resistance_levels=tuple(r for r in o.structural_resistance_levels if r['unified_level_id']!=gray['unified_level_id']),structural_transition_levels=(gray,))
    r=h.evaluate(a,o)
    for i in r.evaluation.intents:
        assert gray['unified_level_id'] not in i.metadata.get('squeeze_add_levels',[])


def test_candidate_compiles(monkeypatch):
    from src.backend import early_squeeze_price_high_candidate as C
    from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
    from tests.test_early_squeeze_candidate import baseline
    base=configuration_base();base['strategy']['profiles']=[p for p in base['strategy']['profiles'] if p['profile_id']!=C.CONTRACT];monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base',lambda:deepcopy(base))
    payload,canvas,plan=C.build(base,baseline(base))
    _build_configuration_release(canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],configuration=payload,run_plan_id=plan,strategy_profile_id=C.CONTRACT)


def test_broker_stop_ignores_bid_only_cross_and_ineligible_trade():
    import asyncio
    from datetime import timedelta
    from tests.test_trading_runtime import quote, trade, TS
    from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
    from src.trading_runtime.ibkr_schema import OrderRequest
    async def run():
        broker=SimulatedBrokerAdapter(['TEST'],SimulationConfig(initial_cash=10000,liquidity_participation=1))
        await broker.initialize()
        await broker.on_market_event(quote(bid=3.42,ask=3.43))
        await broker.place_orders('TEST',[OrderRequest(acctId='TEST',conid=265598,cOID='buy',ticker='AAPL',orderType='MKT',side='BUY',quantity=10)])
        await broker.on_market_event(replace(quote(bid=3.42,ask=3.43),ts=TS+timedelta(seconds=1)))
        await broker.place_orders('TEST',[OrderRequest(acctId='TEST',conid=265598,cOID='stop',ticker='AAPL',orderType='STP',side='SELL',quantity=10,auxPrice=3.48,raw={'canonical_metadata':{'stop_trigger_source':'eligible_trade'}})])
        assert not await broker.on_market_event(replace(quote(bid=3.45,ask=3.55),ts=TS+timedelta(seconds=2)))
        assert not await broker.on_market_event(replace(trade(price=3.55),ts=TS+timedelta(seconds=3)))
        assert not await broker.on_market_event(replace(trade(price=3.47),raw={'conid':265598,'price_eligible':False},ts=TS+timedelta(seconds=4)))
        fills=await broker.on_market_event(replace(trade(price=3.47),ts=TS+timedelta(seconds=5)))
        assert len(fills)==1
        assert fills[0].price==pytest.approx(3.45)
    asyncio.run(run())


def test_first_entry_does_not_require_new_high_after_quality_delay():
    h,a,t=fixture();a=advance(a,h.evaluate(a,t()))
    # The first crossing fails spread admission and must not consume an entry.
    a=advance(a,h.evaluate(a,replace(t(.01,10.46),ask=11.)))
    assert not a.state['squeeze_breakout'].get('entered_levels')
    r=h.evaluate(a,t(.02,10.44))
    assert any(i.action=='enter_long' for i in r.evaluation.intents)
    assert r.state['squeeze_breakout']['breakout_highs']['R2']==10.46


@pytest.mark.parametrize('action',['entry','add'])
def test_trade_above_ask_offsets_new_protection_below_entry_reference(action):
    from src.trading_runtime.execution_policies import StopRule, StopRuleType
    if action=='entry':
        h,a,t=fixture();a=advance(a,h.evaluate(a,t()))
        o=replace(t(.01,10.44),bid=10.37,ask=10.39)
    else:
        h,a,t=opened()
        a.state['squeeze_entry'].update(trail_distance=1.,peak_price=10.44)
        o=replace(t(.02,10.84,100.),bid=10.70,ask=10.72)
    r=h.evaluate(a,o)
    i=next(i for i in r.evaluation.intents if i.action==('enter_long' if action=='entry' else 'add_long'))
    assert i.invalidation_price==pytest.approx(o.ask-.01)
    assert i.invalidation_price<i.reference_price
    assert StopRule(rule_type=StopRuleType.FIXED_PRICE,price=i.invalidation_price).resolve(reference_price=i.reference_price,side='long',quantity=100)==i.invalidation_price
    if action=='add':
        assert i.invalidation_price>=a.state['active_stop']


def test_sugp_failure_41444_trade_409_ask_offsets_411_stop_to_408():
    h,a,t=fixture()
    def actual(at,price):
        o=t(at,price);prototype=o.structural_resistance_levels[0]
        rows=tuple(dict(prototype,unified_level_id=str(n),lower=lo,upper=hi,role='resistance') for n,(lo,hi) in enumerate([(4.115712839374951,4.131191583329103),(4.255287086882092,4.269974196530766),(4.5,4.52),(4.7,4.72)]))
        market=deepcopy(o.structural_detector_state);market['fast_squeeze_context']['hod']=4.14
        return replace(o,bid=4.07,ask=4.09,structural_resistance_levels=rows,structural_support_levels=(),structural_transition_levels=(),structural_detector_state=market,source_values={**o.source_values,'indicator.vwap.execution_value@100ms':dict(value=3.643785349957355,observed_at=o.observed_at.isoformat())})
    a=advance(a,h.evaluate(a,actual(0.,4.13)))
    r=h.evaluate(a,actual(.01,4.1444));i=next(i for i in r.evaluation.intents if i.action=='enter_long')
    assert i.reference_price==4.09
    assert i.invalidation_price==4.08
    assert i.metadata['structural_stop']==4.11
    for rule in i.protection_profile.slices:
        assert rule.stop.resolve(reference_price=i.reference_price,side='long',quantity=100)==4.08
