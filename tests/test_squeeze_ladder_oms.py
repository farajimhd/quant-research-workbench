import asyncio
from dataclasses import replace
from datetime import timedelta

from src.trading_runtime.domain import InstrumentContract, TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import OrderManagementEngine, _apply_cumulative_fill
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.ibkr_schema import OrderStatus
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_adaptive_execution_risk import portfolio_approved
from tests.test_squeeze_ladder_protection import request
from tests.test_trading_runtime import quote


def test_partial_second_lot_gets_own_target_and_repair_batch_survives_restart(tmp_path):
    async def exercise():
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
        await broker.initialize()
        journal = TradingJournal(tmp_path / "ladder-unit.sqlite3")
        def planner(intent, account_id, event):
            return IbkrStrategyOrderPlanner().plan(account_id=account_id,
                instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
                intent=intent, strategy_id="prepared-ladder", strategy_revision=1)
        managers = []
        async def manager(name):
            risk = RiskAuthority()
            await risk.prime(broker, ["DU1"])
            result = OrderManagementEngine(broker=broker, planner=planner, risk=risk,
                journal=journal, run_id=name, strategy_id="prepared-ladder", strategy_revision=1)
            managers.append(result)
            return result
        try:
            first = await manager("before")
            snapshot = await first.submit_intent(portfolio_approved(journal, request()),
                account_id="DU1", event=None)
            group = first._groups[snapshot.group_id]
            event = replace(quote(bid=9.99, ask=10, ask_size=160), ticker="TEST",
                raw={"conid":123}, ts=request().event_time, ingest_ts=request().event_time)
            await broker.on_market_event(event)
            for order in await broker.live_orders():
                order_id = str(order.orderId)
                if order_id in group.broker_order_roles:
                    _apply_cumulative_fill(group, order_id, float(order.filledQuantity),
                                           group.broker_order_roles[order_id])
            result = await first.reconcile_protection(group)
            repair = [action for action in result["actions"] if action["action"] == "place_ladder_repair_pair"]
            assert len(repair) == 1
            assert repair[0]["lot_id"] == "lot-2"
            assert repair[0]["target_price"] == 10.5
            assert repair[0]["quantity"] == 6
            assert result["required_quantity"] == result["protected_quantity"] == 40
            assert group.plan.order_slice_ids[-2:] == ("lot-2", "lot-2")
            assert len(group.plan.broker_batches[-1]) == 2
            repeated = await first.reconcile_protection(group)
            assert repeated["actions"] == []
            next_at = event.ts + timedelta(milliseconds=100)
            await broker.on_market_event(replace(event, ts=next_at, ingest_ts=next_at,
                                                sequence=2, ask_size=112))
            for order in await broker.live_orders():
                order_id = str(order.orderId)
                if order_id in group.broker_order_roles:
                    _apply_cumulative_fill(group, order_id, float(order.filledQuantity),
                                           group.broker_order_roles[order_id])
            retired = await first.reconcile_protection(group)
            assert any(action["action"] == "retire_ladder_repair_pair" for action in retired["actions"])
            assert retired["required_quantity"] == retired["protected_quantity"] == 68
            repairs = [order for order in await broker.live_orders() if "repair-" in order.cOID]
            assert len(repairs) == 2
            assert all(order.order_status == OrderStatus.CANCELLED for order in repairs)
            await first.close()
            second = await manager("after")
            recovered = await second.recover()
            restored = second._groups[recovered[0].group_id]
            assert restored.plan.order_slice_ids == group.plan.order_slice_ids
            assert restored.plan.broker_batches == group.plan.broker_batches
            assert restored.broker_order_slices == group.broker_order_slices
        finally:
            for item in managers:
                await item.close()
            journal.close()
    asyncio.run(exercise())
