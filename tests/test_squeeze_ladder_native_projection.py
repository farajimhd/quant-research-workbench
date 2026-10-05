"""Qualify ladder ownership against normalized ARTE OMS rows, without writes."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import freeze_oms_group, oms_group_state_batch
from src.trading_runtime.domain import InstrumentContract
from src.trading_runtime.order_management import _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.signals import CapitalRequest
from src.trading_runtime.strategy_orders import IbkrStrategyOrderPlanner
from tests.test_squeeze_ladder_protection import request


def test_native_rows_preserve_one_admission_three_lots_and_repair_pair():
    original = replace(request(), quantity=0.,
                       capital_request=CapitalRequest("mandate_fraction", 1 / 3))
    metadata = dict(assignment_id="assignment-1", portfolio_account_key="cash",
        portfolio_decision_id="decision-1", unprotected_backtest_authorized=False,
        portfolio_policy="policy-1", portfolio_reservation_id="reservation-1",
        requested_quantity=101., portfolio_fx_to_base=1.,
        correlation_id="corr-1", causation_id="decision-1")
    approved = replace(original, quantity=101., metadata=metadata)
    reservation = dict(intent_id=original.intent_id, account_id="DU1",
        reservation_id="reservation-1", decision_id="decision-1",
        assignment_id="assignment-1", status="reserved", quantity=101.)
    plan = IbkrStrategyOrderPlanner().plan(account_id="DU1",
        instrument=InstrumentContract("TEST", 123, "TEST", "STK", "USD"),
        intent=approved, strategy_id="prepared-ladder", strategy_revision=1)
    _, target, stop = plan.broker_batches[1]
    pair = (replace(target, cOID="repair-target-2", parentId=None, quantity=5),
            replace(stop, cOID="repair-stop-2", parentId=None, quantity=5))
    plan = plan.with_lot_repair_pair(lot_id="lot-2", pair=pair)
    at = original.event_time
    group = _ManagedOrderGroup("group-1", approved, "DU1", plan,
        OrderManagementState.CREATED, at, at, list(plan.orders), remaining_quantity=101.)
    run_id, attempt, first_id, second_id = "backtest:ladder-projection", str(uuid4()), str(uuid4()), str(uuid4())
    first = strategy_intent_batch(original, run_id=run_id, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt, batch_id=first_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000", sequence=1,
        source_cursor="intent", run_status="running", recorded_at=at)
    batch = oms_group_state_batch(freeze_oms_group(group), run_id=run_id,
        run_month=date(2026, 8, 1), attempt_id=attempt, batch_id=second_id,
        prior_batch_id=first_id, sequence=2, source_cursor="oms", run_status="running",
        strategy_id="prepared-ladder", strategy_revision=1, recorded_at=at,
        published_intent_batch=first, committed_intent_batch_id=first_id,
        admission_source_intent=original, admission_reservation=reservation)
    assert [row["batch_ordinal"] for row in batch.oms_order_states] == [0]*3 + [1]*3 + [2]*3 + [3]*2
    assert [row["slice_id"] for row in batch.oms_order_states] == ["lot-1"]*3 + ["lot-2"]*3 + ["lot-3"]*3 + ["lot-2"]*2
    assert len(batch.intent_uses) == 1
    assert len(batch.oms_group_states) == 1
