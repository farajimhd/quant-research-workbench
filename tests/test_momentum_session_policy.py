from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.trading_runtime import momentum_session_policy as P
from tests.test_early_squeeze_momentum import momentum_fixture, advance, S, level


def liquid(o):
    values = dict(o.source_values)
    for k, v in [('market.volume', 25000), ('market.session_dollar_volume', 100000),
                 ('market.trade_rate_10s', 1), ('market.trade_rate_60s', .5)]:
        values[k] = dict(value=v, observed_at=o.observed_at.isoformat())
    return replace(o, source_values=values)


def test_purchase_gates_recheck_current_liquidity_and_fail_closed():
    _, _, trade, _, _ = momentum_fixture()
    o = liquid(trade(16.02, 10.44))
    assert P.purchase_gate(o, P.DEFAULTS)['passed']
    assert not P.purchase_gate(replace(o, ask=11.), P.DEFAULTS)['passed']
    assert not P.purchase_gate(replace(o, observed_at=o.observed_at+timedelta(seconds=3)), P.DEFAULTS)['passed']
    for k in ('market.volume', 'market.session_dollar_volume', 'market.trade_rate_10s', 'market.trade_rate_60s'):
        values = deepcopy(o.source_values)
        values[k]['value'] = 0
        assert k in P.purchase_gate(replace(o, source_values=values), P.DEFAULTS)['failed']


def test_engine_waits_after_activation_until_purchase_gate_passes():
    h, a, trade, _, _ = momentum_fixture()
    a = replace(a, parameters={**a.parameters, 'momentum_full_session': P.DEFAULTS})
    o = trade(16.02, 10.44)
    values = deepcopy(o.source_values)
    values.pop('market.volume', None)
    result = h.evaluate(a, replace(o, source_values=values))
    assert not result.evaluation.intents
    assert result.evaluation.signals[0].reason == 'post_squeeze_liquidity_gate'
    a = advance(a, result)
    result = h.evaluate(a, liquid(trade(16.03, 10.44)))
    assert result.evaluation.intents[0].action == 'enter_long'


def age(active, now, green, rows=None):
    return P.aged_action(active, rows or {}, now=now, bid=11., tick=.01,
        net=dict(status='verified', net_pnl=1 if green else -1, break_even=10.1), policy=P.DEFAULTS)


def test_red_grace_absolute_deadline_and_recovery():
    active = dict(first_fill_at=0.)
    assert age(active, 299.9, False) is None
    assert age(active, 300, False)['action'] == 'hold'
    assert age(active, 359.9, False)['action'] == 'hold'
    assert age(active, 360, False)['reason'] == 'aged_red_grace_expired'
    active = dict(first_fill_at=0.)
    age(active, 300, False)
    assert age(active, 310, True)['reason'] == 'aged_red_recovered_green'
    assert age(dict(first_fill_at=0.), 400, False)['action'] == 'exit'


def test_green_bracket_uses_current_resistances_and_freezes_target():
    rows = dict(low=level('low',10.5,10.6), high=level('high',11.3,11.4),
        gray=level('gray',10.8,10.9,'support'))
    active = dict(first_fill_at=0.)
    result = age(active, 300, True, rows)
    assert result == dict(action='protect', stop=10.49, target=11.3)
    assert age(active, 301, True, {}) == result
    assert age(dict(first_fill_at=0.), 300, True)['reason'] == 'aged_green_no_valid_resistance_bracket'


def test_gap_funding_ranks_smaller_green_oldest_and_protects_young_red():
    rows = [dict(ticker=t, gap=g, age=a, net_pnl=n) for t,g,a,n in [
        ('larger',3,900,20),('equal',2,900,20),('youngred',1,359,-1),
        ('oldred',1,360,-1),('younggreen',1,20,20),('oldgreen',1,301,1)]]
    assert [r['ticker'] for r in P.funding_rank(rows,2)] == ['oldgreen','younggreen','oldred']


def test_net_green_includes_final_entry_fees_and_estimated_exit_fee():
    _, _, trade, _, _ = momentum_fixture()
    at = trade().observed_at
    row = SimpleNamespace(account_id='A', instrument=SimpleNamespace(conid=1,currency='USD'),
        run_id='R', source_event_time=at, commission_status='final', commission=Decimal('1'),
        commission_currency='USD', side='BUY', quantity=Decimal('100'), price=Decimal('10'))
    args = dict(account_id='A',conid=1,run_id='R', first_fill_at=at.timestamp(),now=at.timestamp(),
        quantity=100,bid=10.01,policy=P.DEFAULTS)
    result = P.net_position([row], **args)
    assert result['net_pnl'] == pytest.approx(-1)
    assert result['break_even'] == pytest.approx(10.02)
    assert P.net_position([row], **{**args,'bid':10.03})['net_pnl'] > 0
    row.commission_status = 'pending'
    assert P.net_position([row], **args)['status']=='unavailable'


def test_engine_age_exit_does_not_need_macd_or_purchase_liquidity():
    h, a, trade, _, _ = momentum_fixture()
    a = advance(a,h.evaluate(a,trade(16.02,10.44)))
    a = replace(a,status=S.AssignmentStatus.MANAGING,
        parameters={**a.parameters,'momentum_full_session':P.DEFAULTS})
    a.state['squeeze_entry']['first_fill_at'] = trade(16.02).observed_at.timestamp()
    o = replace(trade(316.02,10.44,100),momentum_position_net=dict(status='verified',net_pnl=-1,break_even=10.5))
    a = advance(a,h.evaluate(a,o))
    assert a.state['squeeze_entry']['age_mode']=='red'
    result = h.evaluate(a,replace(trade(376.02,10.44,100),momentum_position_net=o.momentum_position_net))
    assert result.evaluation.intents[0].reason == 'aged_red_grace_expired'


@pytest.mark.parametrize('action', ['enter_long', 'add_long'])
def test_runtime_funding_chooses_one_smaller_gap_and_waits_for_proceeds(action):
    import asyncio
    from unittest.mock import AsyncMock
    from src.trading_runtime.runtime import TradingRuntime
    from tests.test_order_management import intent as make_intent
    async def run():
        at = make_intent().event_time
        def assignment(ticker, gap, age):
            return SimpleNamespace(account_id='A',ticker=ticker,conid=ord(ticker),assignment_id=ticker,
                parameters=dict(momentum_full_session=P.DEFAULTS), permissions=SimpleNamespace(exit=True),
                state=dict(squeeze_entry=dict(first_fill_at=at.timestamp()-age,average_gap=gap)))
        rows = [assignment('B', 1, 301), assignment('C', 3, 500), assignment('D', 1, 30)]
        runtime = SimpleNamespace(strategy=SimpleNamespace(assignments=lambda: rows),
            order_manager=SimpleNamespace(working_exit_quantity=lambda *args: 0),
            broker=SimpleNamespace(positions=AsyncMock(return_value=[SimpleNamespace(conid=a.conid,position=100) for a in rows])),
            execution_market_data=SimpleNamespace(snapshot=lambda ticker:SimpleNamespace(bid=10,observed_at=at)),
            _momentum_net=lambda *args:dict(status='verified',net_pnl=10),
            _execute_intents=AsyncMock())
        request = replace(make_intent(action=action),ticker='NEW',metadata=dict(momentum_full_session=True,
            momentum_target=dict(average_gap=2)))
        assert await TradingRuntime._fund_momentum_request(runtime,request,'A',
            SimpleNamespace(reasons=['limited_by_available_funds']))
        evaluation = runtime._execute_intents.call_args.args[0]
        assert len(evaluation.intents)==1
        assert evaluation.intents[0].ticker=='B'
        assert evaluation.intents[0].action=='exit'
        assert evaluation.intents[0].metadata['displaced_gap']==1
        assert evaluation.intents[0].capital_request is None
        # No original purchase is submitted on the promise of proceeds.
        assert runtime._execute_intents.call_count==1
        runtime._execute_intents.reset_mock()
        assert not await TradingRuntime._fund_momentum_request(runtime,request,'A',
            SimpleNamespace(reasons=['limited_by_position']))
        runtime._execute_intents.assert_not_called()
    asyncio.run(run())


def test_capital_add_retry_rechecks_level_and_macd_episode():
    from tests.test_early_squeeze_momentum import M
    h, a, trade, _, _ = momentum_fixture()
    a = replace(a,parameters={**a.parameters,'momentum_full_session':P.DEFAULTS,
        'momentum_session_progression':True})
    result = h.evaluate(a,liquid(trade(16.02,10.44)))
    episode = result.evaluation.intents[0].metadata['macd_1s_episode']['episode_id']
    a = advance(a,result)
    a = replace(a,status=S.AssignmentStatus.MANAGING)
    row = M.levels(trade(16.04))['R2']
    a.state['squeeze_breakout']['capital_add'] = dict(confirmation=dict(level=row),episode_id=episode)
    result = h.evaluate(a,liquid(trade(16.04,10.44,100)))
    assert any(i.action=='add_long' for i in result.evaluation.intents)
    a.state['squeeze_breakout']['capital_add']['episode_id'] = 'expired'
    result = h.evaluate(a,liquid(trade(16.04,10.44,100)))
    assert not any(i.action=='add_long' for i in result.evaluation.intents)


def test_non_cash_limits_cannot_trigger_liquidation():
    assert not P.cash_shortfall(['limited_by_position','quantity_below_minimum_increment'])
    assert P.cash_shortfall(['limited_by_available_funds','quantity_below_minimum_increment'])
