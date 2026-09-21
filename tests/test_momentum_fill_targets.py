import asyncio
from dataclasses import replace

import pytest

from tests import test_order_management as helpers
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.execution_policies import ProtectionProfile, ProtectionSlice, StopRule, StopRuleType
from src.trading_runtime.order_management import BrokerCommunicationPolicy
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner
from src.trading_runtime.ibkr_schema import OPEN_ORDER_STATUSES


@pytest.mark.parametrize('canonical', [False, True])
@pytest.mark.parametrize('signal_reference', [9.92, 10.4])
def test_actual_fill_targets_amendments_and_repair_keep_tranche_authority(tmp_path, canonical, signal_reference):
    async def run():
        broker = SimulatedBrokerAdapter(['DU1'], mode=TradingMode.BACKTEST)
        manager, journal = await helpers.OrderManagementPolicyTests()._manager(
            str(tmp_path), broker, policy=BrokerCommunicationPolicy(), causal_execution_clock=True)
        planner = RuntimeIbkrStrategyOrderPlanner({'TEST': InstrumentContract('TEST', 123, 'TEST', 'STK', 'USD')},
            strategy_id='strategy-1', strategy_revision=1)
        manager.planner = lambda intent, account, event: planner.plan(intent=intent, account_id=account, event=event)
        groups = []
        try:
            for n, fills in enumerate([[(10.0, 40.), (10.1, 60.)], [(10.3, 100.)]]):
                request = helpers.intent(action='enter_long' if n == 0 else 'add_long', quantity=100.)
                request = replace(request, intent_id=f'momentum-{n}', reference_price=signal_reference,
                    invalidation_price=9.9, profit_target_price=11.4,
                    protection_profile=ProtectionProfile('momentum-test', 1, slices=(ProtectionSlice(
                        'all', 1., StopRule(StopRuleType.FIXED_PRICE, price=9.9), profit_target_price=11.4),)),
                    metadata={**request.metadata, 'momentum_target': dict(average_gap=.2, multiplier=5, tick_size=.01),
                        'momentum_initial_stop': {'reason':'one_percent_entry_stop'} if n == 0 else None,
                        'stop_exit_reason':'one_percent_entry_stop', 'mandatory_broker_target': True})
                snapshot = await manager.submit_intent(helpers.portfolio_approved(journal, request), account_id='DU1', event=None)
                group = manager._groups[snapshot.group_id]
                root = next(k for k, v in group.broker_order_roles.items() if v == 'entry')
                for price, quantity in fills:
                    broker._apply_fill(broker._orders[root], helpers.NOW, price, quantity)
                    order = next(o for o in await broker.live_orders() if str(o.orderId) == root)
                    if canonical:
                        from src.trading_runtime.ibkr_normalizer import normalize_order
                        await manager._on_canonical_order_state(normalize_order(order.to_cpapi()))
                    else:
                        await manager.on_order_update(order)
                    await manager.on_order_update(order)
                groups.append(group)
            assert [g.intent.profit_target_price for g in groups] == pytest.approx([11.06, 11.3])
            assert groups[0].intent.invalidation_price == pytest.approx(9.95)
            assert groups[0].intent.reference_price == signal_reference
            amendment = replace(helpers.intent(action='replace_profit_target', quantity=200.),
                reference_price=13., profit_target_price=11.78,
                metadata={'momentum_target_multiplier': 8})
            await manager.submit_intent(helpers.portfolio_approved(journal, amendment), account_id='DU1', event=None)
            assert [g.intent.profit_target_price for g in groups] == pytest.approx([11.66, 11.9])
            for group in groups:
                targets = [o for o in await broker.live_orders() if group.broker_order_roles.get(str(o.orderId)) == 'profit_target'
                    and o.order_status in OPEN_ORDER_STATUSES]
                assert targets
                for target in targets:
                    request = group.orders[group.broker_order_request_indexes[str(target.orderId)]]
                    assert request.raw['canonical_metadata']['exit_reason'] == 'momentum_target_8x'
                stop = next(o for o in await broker.live_orders() if group.broker_order_roles.get(str(o.orderId)) == 'protective_stop'
                    and o.order_status in OPEN_ORDER_STATUSES)
                await broker.cancel_order('DU1', str(stop.orderId))
                await manager.reconcile_protection(group)
                repairs = [r for r in group.orders if r.orderType == 'STP']
                assert repairs[-1].raw['canonical_metadata']['exit_reason'] == 'one_percent_entry_stop'
                assert group.intent.metadata['momentum_target']['multiplier'] == 8
        finally:
            await manager.close()
            journal.close()
    asyncio.run(run())
