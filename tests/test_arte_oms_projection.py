from __future__ import annotations

from dataclasses import fields, replace
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_writer import (
    _sealed_families, load_committed_prefix, publish_typed_batch,
)
from src.trading_runtime.arte_oms_projection import (
    load_committed_oms_group_state_page, oms_group_state_batch,
    oms_intent_fingerprint,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import _ManagedOrderGroup, OrderManagementState
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from tests.test_arte_intent_projection import intent
from tests.test_arte_journal_writer import MemoryClient


def test_oms_projection_inventory_exposes_unmodeled_runtime_state() -> None:
    represented = {
        "group_id", "intent", "account_id", "plan", "state", "created_at",
        "updated_at", "orders", "broker_order_ids", "broker_order_roles",
        "broker_order_slices", "broker_order_request_indexes",
        "filled_by_broker_order", "terminal_broker_order_ids",
        "warning_message_ids", "rejection_reason", "submitted_at",
        "filled_quantity", "remaining_quantity", "decision_to_submit_ms",
        "reprice_count", "last_reprice_at", "current_limit_price",
        "deferred_reprice", "failed_reprice_at", "internal_reaction_ms",
        "high_water_price", "low_water_price",
        "protection_required_quantity", "protection_coverage_quantity",
        "protection_delegated",
    }
    still_unmodeled = {
        "tactic", "broker_order_state_fingerprints", "reprice_task",
        "reprice_event", "protection_task",
    }
    assert {field.name for field in fields(_ManagedOrderGroup)} == represented | still_unmodeled


def test_oms_group_projection_has_normalized_children_and_committed_fence() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    run_id, attempt_id = "live:oms-test", str(uuid4())
    first_id, second_id = str(uuid4()), str(uuid4())
    strategy_intent = intent(ticker="Test.a")
    intent_fingerprint = oms_intent_fingerprint(strategy_intent)
    first = strategy_intent_batch(
        strategy_intent, run_id=run_id, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=attempt_id, batch_id=first_id,
        prior_batch_id="00000000-0000-0000-0000-000000000000",
        sequence=1, source_cursor="intent", run_status="running", recorded_at=at,
    )
    orders = (
        OrderRequest(acctId="DU1", conid=123, cOID="entry-1", ticker="Test.a",
                     orderType="LMT", side="BUY", quantity=5, price=12.5),
        OrderRequest(acctId="DU1", conid=123, cOID="stop-1", ticker="Test.a",
                     orderType="STP", side="SELL", quantity=5, auxPrice=11.5),
    )
    plan = StrategyOrderPlan(orders, cancel_oca_groups=("old-oca",),
                             batches=((orders[0],), (orders[1],)),
                             order_slice_ids=("entry", "stop"))
    group = _ManagedOrderGroup(
        group_id="group-1", intent=strategy_intent, account_id="DU1", plan=plan,
        state=OrderManagementState.CREATED, created_at=at, updated_at=at,
        orders=list(orders), broker_order_ids=["broker-1"],
        broker_order_roles={"broker-1": "entry"},
        broker_order_request_indexes={"broker-1": 0},
        filled_by_broker_order={"broker-1": 1.0},
        warning_message_ids=["warn-1"], remaining_quantity=4.0,
    )
    second = oms_group_state_batch(
        group, run_id=run_id, run_month=date(2026, 8, 1),
        attempt_id=attempt_id, batch_id=second_id, prior_batch_id=first_id,
        sequence=2, source_cursor="oms", run_status="completed",
        strategy_id="strategy-1", strategy_revision=1, recorded_at=at,
        published_intent_fingerprint=intent_fingerprint,
    )
    assert [row["batch_ordinal"] for row in second.oms_order_states] == [0, 1]
    assert [row["ticker"] for row in second.oms_order_states] == ["Test.a", "Test.a"]
    assert second.oms_broker_bindings[0]["has_filled_quantity"] == 1
    assert second.oms_warnings[0]["message_id"] == "warn-1"
    assert second.oms_cancel_ocas[0]["oca_group"] == "old-oca"
    assert dict(_sealed_families(second))["trading_oms_group_state_v1"]
    client = MemoryClient()
    with pytest.raises(RuntimeError, match="one committed strategy intent"):
        publish_typed_batch(client, second)
    assert client.inserts == []
    publish_typed_batch(client, first)
    publish_typed_batch(client, second)
    prefix = load_committed_prefix(client, run_id)
    assert prefix is not None and prefix.last_sequence == 2
    assert len(client.tables["trading_oms_order_state_v1"]) == 2
    recovered = load_committed_oms_group_state_page(client, prefix)
    assert len(recovered) == 1
    assert recovered[0].orders == orders
    assert recovered[0].order_batch_ordinals == (0, 1)
    assert recovered[0].order_slice_ids == ("entry", "stop")
    assert recovered[0].warning_message_ids == ("warn-1",)
    assert recovered[0].cancel_oca_groups == ("old-oca",)
    assert load_committed_oms_group_state_page(client, prefix, after_sequence=2) == ()
    with pytest.raises(RuntimeError, match="total child budget"):
        load_committed_oms_group_state_page(client, prefix, max_children=4)
    client.tables["trading_oms_order_state_v1"][0]["ticker"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs"):
        load_committed_prefix(client, run_id)
    with pytest.raises(ValueError, match="unmodeled raw"):
        oms_group_state_batch(
            replace(group, orders=[replace(orders[0], raw={"canonical_metadata": {}}), orders[1]]),
            run_id=run_id, run_month=date(2026, 8, 1), attempt_id=attempt_id,
            batch_id=str(uuid4()), prior_batch_id=first_id, sequence=2,
            source_cursor="oms", run_status="running", strategy_id="strategy-1",
            strategy_revision=1, recorded_at=at,
            published_intent_fingerprint=intent_fingerprint,
        )
    with pytest.raises(ValueError, match="differs from its published typed revision"):
        oms_group_state_batch(
            replace(group, intent=replace(strategy_intent, reference_price=12.6)),
            run_id=run_id, run_month=date(2026, 8, 1), attempt_id=attempt_id,
            batch_id=str(uuid4()), prior_batch_id=first_id, sequence=2,
            source_cursor="oms", run_status="running", strategy_id="strategy-1",
            strategy_revision=1, recorded_at=at,
            published_intent_fingerprint=intent_fingerprint,
        )
