import asyncio
from math import floor
from dataclasses import replace

import pytest

from tests import test_order_management as helpers
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ProtectionProfile, ProtectionSlice, StopRule, StopRuleType
from src.trading_runtime.order_management import BrokerCommunicationPolicy
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.r1_ladder import stop_price
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES


@pytest.mark.parametrize('entry,swing,fills,canonical,tight', [
    (2.,1.95,[(1.99,100.)],False,False),
    (2.1,1.5,[(2.05,40.),(1.9,60.)],False,False),
    (2.1,1.5,[(1.9,40.),(2.05,60.)],True,False),
    (2.1,1.5,[(2.005,40.)],False,False),
    (2.1,1.5,[(2.005,40.),(2.04,60.)],False,True),
    (2.1,1.5,[(2.04,40.),(2.005,60.)],True,True),
])
def test_actual_partial_fills_rebase_fixed_stop_and_repair(tmp_path,entry,swing,fills,canonical,tight):
    async def run():
        broker = SimulatedBrokerAdapter(['DU1'],mode=TradingMode.BACKTEST)
        manager,journal = await helpers.OrderManagementPolicyTests()._manager(
            str(tmp_path),broker,policy=BrokerCommunicationPolicy(),causal_execution_clock=True)
        planner = IbkrStrategyOrderPlanner()
        manager.planner = lambda intent,account,event: planner.plan(account_id=account,
            instrument=InstrumentContract('TEST',123,'TEST','STK','USD'),intent=intent,
            strategy_id='strategy-1',strategy_revision=1)
        initial_stop = stop_price(entry,swing,.01)
        request = helpers.intent(side_quote=(entry-.01,entry),quantity=100.)
        request = replace(request,reference_price=entry,invalidation_price=initial_stop,
            profit_target_price=3.,protection_profile=ProtectionProfile('r1-test',1,slices=(
                ProtectionSlice('all',1.,StopRule(StopRuleType.FIXED_PRICE,price=initial_stop),profit_target_price=3.),)),
            metadata={**request.metadata,'r1_stop_bounds':dict(swing_lower=swing,tick_size=.01),
                      'mandatory_broker_target':True})
        if tight:
            metadata=dict(request.metadata);metadata.pop('r1_stop_bounds')
            request=replace(request,metadata={**metadata,'tight_reentry_stop':dict(tick_size=.01)})
        cost_average_key='tight_reentry_average' if tight else 'r1_actual_entry_average'
        try:
            snap = await manager.submit_intent(helpers.portfolio_approved(journal,request),account_id='DU1',event=None)
            group = manager._groups[snap.group_id]
            root_id = next(k for k,v in group.broker_order_roles.items() if v=='entry')
            quantity=notional=0.
            for price,size in fills:
                broker._apply_fill(broker._orders[root_id],helpers.NOW,price,size)
                order = next(x for x in await broker.live_orders() if str(x.orderId)==root_id)
                if canonical:
                    from src.trading_runtime.ibkr_normalizer import normalize_order
                    updated = await manager._on_canonical_order_state(normalize_order(order.to_cpapi()))
                else:
                    updated = await manager.on_order_update(order)
                quantity+=size;notional+=price*size
                desired=(floor((notional/quantity-.01)/.01+1e-9)*.01 if tight else stop_price(notional/quantity,swing,.01))
                if desired is None:
                    assert updated.r1_stop_error == 'actual_fill_stop_not_representable'
                    desired=initial_stop
                    current_root=next(x for x in await broker.live_orders() if str(x.orderId)==root_id)
                    assert current_root.order_status == helpers.OrderStatus.CANCELLED
                else:
                    assert updated.r1_stop_error == ''
                assert (updated.tight_reentry_stop if tight else updated.r1_initial_stop) == pytest.approx(desired)
                if not tight:
                    assert updated.r1_actual_entry_average == pytest.approx(notional/quantity)
                assert group.intent.invalidation_price == pytest.approx(desired)
                assert group.intent.metadata[cost_average_key] == pytest.approx(notional/quantity)
                stops=[x for x in await broker.live_orders() if group.broker_order_roles.get(str(x.orderId))=='protective_stop'
                       and x.order_status in OPEN_ORDER_STATUSES]
                assert stops and all(x.auxPrice == pytest.approx(desired) for x in stops)
                await manager.on_order_update(order)  # Duplicate cannot change cumulative cost.
                assert group.intent.metadata[cost_average_key] == pytest.approx(notional/quantity)
                if canonical and quantity < 100:
                    await manager.recover()
                    group=manager._groups[snap.group_id]
                    assert group.intent.metadata[cost_average_key] == pytest.approx(notional/quantity)
            stop=next(x for x in await broker.live_orders() if group.broker_order_roles.get(str(x.orderId))=='protective_stop' and x.order_status in OPEN_ORDER_STATUSES)
            await broker.cancel_order('DU1',str(stop.orderId))
            await manager.reconcile_protection(group)
            repaired=[x for x in await broker.live_orders() if x.orderType=='STP' and x.order_status in OPEN_ORDER_STATUSES]
            assert repaired and all(x.auxPrice == pytest.approx(desired) for x in repaired)
        finally:
            await manager.close();journal.close()
    asyncio.run(run())
