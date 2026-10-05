from datetime import datetime, timezone
from decimal import Decimal as D
import asyncio

import pytest

from src.trading_runtime.arte_intent_projection import project_strategy_intent, restore_strategy_intent
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.squeeze_ladder_protection import (
    ladder_profile, ladder_weights, percentage_ladder, structural_ladder,
)
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.risk import RiskAuthority
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from tests.test_adaptive_execution_risk import portfolio_approved


def request(quantity=101):
    return StrategyIntent(
        intent_id="ladder-entry", ticker="TEST",
        event_time=datetime(2026, 8, 18, 12, 5, tzinfo=timezone.utc),
        action="enter_long", quantity=quantity, reference_price=10,
        outside_rth=True,
        protection_profile=ladder_profile(D("10"), D("9.5"),
                                         (D("10.25"), D("10.5"), D("11"))),
    )


def test_actual_planner_preserves_total_quantity_and_independent_brackets():
    plan = IbkrStrategyOrderPlanner().plan(
        account_id="DU1", instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
        intent=request(), strategy_id="prepared-ladder", strategy_revision=1,
    )
    assert len(plan.orders) == 9
    assert [batch[0].quantity for batch in plan.broker_batches] == [34, 34, 33]
    assert len({batch[0].cOID for batch in plan.broker_batches}) == 3
    for batch, target in zip(plan.broker_batches, (10.25, 10.5, 11), strict=True):
        parent, profit, stop = batch
        assert profit.parentId == stop.parentId == parent.cOID
        assert profit.quantity == stop.quantity == parent.quantity
        assert profit.price == target
        assert stop.auxPrice == 9.5
        assert all(order.outsideRTH for order in batch)
    assert plan.order_slice_ids == tuple(f"lot-{j}" for j in (1, 2, 3) for _ in range(3))


def test_insufficient_quantity_cannot_create_empty_protected_lots():
    with pytest.raises(ValueError):
        IbkrStrategyOrderPlanner().plan(
            account_id="DU1", instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
            intent=request(2), strategy_id="prepared-ladder", strategy_revision=1,
        )


def test_three_lots_survive_normalized_journal_roundtrip():
    source = request()
    projected = project_strategy_intent(source)
    assert projected.core["protection_slice_count"] == 3
    assert restore_strategy_intent(projected) == source
    assert all(not isinstance(value, (dict, list, tuple))
               for row in (projected.core, *projected.protection_slices)
               for value in row.values())


def test_tick_geometry_is_directional_and_does_not_merge_targets():
    assert percentage_ladder(D("0.1234"), (D(".025"), D(".05"), D(".10")),
                             D(".0001")) == (D(".1265"), D(".1296"), D(".1358"))
    assert structural_ladder(D("10"), ("v7-a", "v7-b", "v7-c"),
                             (D("10.257"), D("10.508"), D("11.001")),
                             D(".01")) == (D("10.24"), D("10.49"), D("10.99"))
    with pytest.raises(ValueError):
        percentage_ladder(D("1"), (D(".001"), D(".002")), D(".01"))
    with pytest.raises(ValueError):
        structural_ladder(D("10"), ("same", "same"), (D("11"), D("12")), D(".01"))


@pytest.mark.parametrize("allocation", ["equal", "increasing", "decreasing"])
@pytest.mark.parametrize("count", [2, 3, 5])
def test_allocation_conserves_one_approved_budget(count, allocation):
    weights = ladder_weights(count, allocation)
    assert sum(weights) == pytest.approx(1)
    assert all(weight > 0 for weight in weights)
    if allocation == "increasing":
        assert list(weights) == sorted(weights)
    elif allocation == "decreasing":
        assert list(weights) == sorted(weights, reverse=True)
    else:
        assert len(set(weights)) == 1


@pytest.mark.parametrize("stop", [D("NaN"), D("Infinity"), D("0"), D("10")])
def test_invalid_stop_fails_before_order_planning(stop):
    with pytest.raises(ValueError):
        ladder_profile(D("10"), stop, (D("11"), D("12")))


def test_native_float_conversion_cannot_silently_collapse_distinct_targets():
    with pytest.raises(ValueError, match="collapsed"):
        ladder_profile(D("10"), D("9"), (D("11"), D("11.00000000000000000001")))


def test_actual_oms_submission_and_cold_recovery_preserve_lot_brackets(tmp_path):
    """Exercise shared OMS contracts; this unit journal is not app authority."""
    async def exercise():
        broker = SimulatedBrokerAdapter(["DU1"], mode=TradingMode.PAPER)
        await broker.initialize()
        journal = TradingJournal(tmp_path / "ladder-unit.sqlite3")
        instrument = InstrumentContract("TEST", 123, "TEST", "STK", "USD")
        def planner(intent, account_id, _event):
            return IbkrStrategyOrderPlanner().plan(
                account_id=account_id, instrument=instrument, intent=intent,
                strategy_id="prepared-ladder", strategy_revision=1)
        managers = []
        try:
            for name in ("before", "after"):
                risk = RiskAuthority()
                await risk.prime(broker, ["DU1"])
                manager = OrderManagementEngine(
                    broker=broker, planner=planner, risk=risk, journal=journal,
                    run_id=name, strategy_id="prepared-ladder", strategy_revision=1)
                managers.append(manager)
                if name == "before":
                    group = await manager.submit_intent(
                        portfolio_approved(journal, request()), account_id="DU1", event=None)
                    assert len(group.broker_order_ids) == 9
                    original = manager._groups[group.group_id]
                    assert len(original.plan.broker_batches) == 3
                    await manager.close()
                else:
                    recovered = await manager.recover()
                    assert len(recovered) == 1
                    restored = recovered[0]
                    assert restored.group_id == group.group_id
                    assert restored.broker_order_ids == group.broker_order_ids
                    restored_state = manager._groups[restored.group_id]
                    assert restored_state.broker_order_slices == original.broker_order_slices
                    assert restored_state.plan.order_slice_ids == original.plan.order_slice_ids
                    assert restored_state.plan.broker_batches == original.plan.broker_batches
        finally:
            for manager in managers:
                await manager.close()
            journal.close()
    asyncio.run(exercise())
