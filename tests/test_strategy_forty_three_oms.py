import asyncio
from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.ibkr_schema import OrderStatus
from src.trading_runtime.order_management import (
    OrderManagementEngine, OrderManagementState, _ManagedOrderGroup,
)
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.strategy_forty_three_oms import LegStopAmendment, replace_leg_stop
from src.trading_runtime.strategy_forty_three_rules import entry_intents, propose_batch
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_strategy_forty_three_rules import facts


async def setup():
    batch = propose_batch(facts(), account_id="A", assignment_id="X",
        free_cash_after_reservations=10_000., already_submitted=False)
    intents = entry_intents(batch, session_date=date(2026, 9, 3))
    broker = SimulatedBrokerAdapter(["A"], mode=TradingMode.BACKTEST,
        initial_time=intents[0].event_time, fixed_bar_mode=True)
    await broker.initialize()
    journal = BacktestMemoryJournal(run_id="native-43-oms-test")
    manager = OrderManagementEngine(broker=broker, planner=lambda *_: None,
        risk=RiskAuthority(), journal=journal, run_id=journal.run_id,
        strategy_id="squeeze-grid-strategy", strategy_revision=43,
        causal_execution_clock=True)
    contract = InstrumentContract("conid:123", 123, "TEST", "STK", "SMART", "USD")
    for ordinal, intent in enumerate(intents, 1):
        # Native exchange fixture. Portfolio admission/publication is tested
        # independently; this test qualifies group isolation and OCA sizing.
        plan = IbkrStrategyOrderPlanner().plan(intent=intent,
            instrument=contract, account_id="A", strategy_id="squeeze-grid-strategy",
            strategy_revision=43)
        responses = await broker.place_orders("A", list(plan.orders))
        group = _ManagedOrderGroup(f"g{ordinal}",
            replace(intent, metadata={"assignment_id": "X"}), "A", plan,
            OrderManagementState.WORKING, intent.event_time, intent.event_time,
            list(plan.orders), submitted_at=intent.event_time)
        manager._remember_group(group)
        for index, (request, response) in enumerate(zip(plan.orders, responses)):
            identity = str(response["order_id"])
            group.broker_order_ids.append(identity)
            group.broker_order_request_indexes[identity] = index
            manager._group_by_broker_id[identity] = group.group_id
            manager._group_by_client_id[request.cOID] = group.group_id
            role = "entry" if index == 0 else "protective_stop" if request.orderType == "STP" else "profit_target"
            group.broker_order_roles[identity] = role
    return manager, broker, journal


def test_one_native_stop_amendment_leaves_fourteen_siblings_and_all_targets_unchanged():
    async def run():
        manager, broker, journal = await setup()
        before = {str(order.orderId): (order.auxPrice, order.price, order.filledQuantity + order.remainingQuantity)
                  for order in await broker.live_orders()}
        group = manager._groups["g1"]
        stop_id = next(key for key, role in group.broker_order_roles.items() if role == "protective_stop")
        held = broker._orders[stop_id]
        # Simulate an OCA-reduced held stop: its original request in OMS is
        # larger. The amendment must use the current broker-held total.
        held.request = replace(held.request, quantity=5.)
        held.filled = 2.
        held.status = OrderStatus.SUBMITTED
        intent = StrategyIntent("stop-43", "TEST", group.intent.event_time + timedelta(seconds=11),
            "replace_protective_stop", 3., 10.5, invalidation_price=10.2,
            reason="strategy_forty_three_adaptive_stop")
        source = LegStopAmendment(group.group_id, group.intent.intent_id, "X", "A", intent)
        await replace_leg_stop(manager, source)
        after = {str(order.orderId): (order.auxPrice, order.price, order.filledQuantity + order.remainingQuantity)
                 for order in await broker.live_orders()}
        assert after[stop_id][0] == 10.2
        assert after[stop_id][2] == 5.
        assert {key: value for key, value in before.items() if key != stop_id} == {
            key: value for key, value in after.items() if key != stop_id}
        amendments = [row for row in journal.records(journal.run_id)
                      if row.entity_type == "protection_change"]
        assert [row.payload["phase"] for row in amendments] == ["requested", "effective"]
        assert all(row.payload["order_group_id"] == "g1" for row in amendments)
        acknowledgements = [row for row in journal.records(journal.run_id)
                            if row.entity_type == "order_acknowledgement"]
        assert len(acknowledgements) == 1
        assert acknowledgements[0].payload["intent_id"] == "stop-43"
        # An idempotent receipt retry must preserve the effective stop.
        await replace_leg_stop(manager, replace(source,
            intent=replace(intent, invalidation_price=10.1)))
        assert manager._groups["g1"].intent.invalidation_price == 10.2
        await manager.close()
        journal.close()
    asyncio.run(run())


def test_wrong_group_source_fails_before_broker_modification():
    async def run():
        manager, broker, journal = await setup()
        group = manager._groups["g1"]
        intent = StrategyIntent("stop-43", "TEST", group.intent.event_time + timedelta(seconds=11),
            "replace_protective_stop", 1., 10.5, invalidation_price=10.2,
            reason="strategy_forty_three_adaptive_stop")
        with pytest.raises(ValueError, match="differs"):
            await replace_leg_stop(manager, LegStopAmendment("g2", group.intent.intent_id, "X", "A", intent))
        assert not journal.records(journal.run_id)
        await manager.close()
        journal.close()
    asyncio.run(run())
