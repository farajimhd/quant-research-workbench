"""Pure V4 compound preparation before any ClickHouse or Keeper mutation."""
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, prepare_compound_v4_families, publish_compound_v4,
)
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.backend.backtest_typed_publisher import (
    _coalesce_v4_units, _committed_intent_source,
)
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, typed_row
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_oms_projection import (
    load_committed_oms_group_state_page, oms_group_state_batch,
)
from src.trading_runtime.arte_oms_tactic_projection import (
    V4OmsTacticBatch, tactic_rows,
)
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from src.trading_runtime.arte_broker_acknowledgement_v4 import (
    ACKNOWLEDGEMENT, broker_acknowledgement_batch_v4,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.order_management import (
    _ManagedOrderGroup, OrderManagementState, ExecutionQuote,
    ExecutionTactic, ExecutionUrgency, PriceStep,
)
from src.trading_runtime.strategy_orders import StrategyOrderPlan
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from tests.test_arte_journal_writer import batch
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_intent_projection import intent


RUN = str(UUID(int=1))
ATTEMPT = str(UUID(int=2))
NIL = str(UUID(int=0))


def _base(sequence, batch_number, prior_number, *, kind=("risk", "continuous_risk_state")):
    batch_id = str(UUID(int=batch_number))
    return TypedJournalBatch(
        RUN, date(2026, 8, 1), ATTEMPT, batch_id,
        str(UUID(int=prior_number)), sequence, sequence, "start", "running",
        ({"record_id": str(UUID(int=100 + sequence)), "run_id": RUN,
          "batch_id": batch_id, "category": kind[0],
          "entity_type": kind[1], "sequence": sequence},),
    )


def test_compound_rekeys_mixed_scalar_children_without_mutating_sources():
    first = _base(1, 11, 0)
    second = _base(2, 12, 11,
                   kind=("portfolio_management", "portfolio_allocation"))
    allocation = {"record_id": second.events[0]["record_id"], "run_id": RUN,
                  "batch_id": second.batch_id, "content_hash": "old-seal"}
    result = coalesce_v4_units((first, V4PortfolioAllocationBatch(second, allocation)))

    assert result.base.batch_id == second.batch_id
    assert (result.base.first_sequence, result.base.last_sequence) == (1, 2)
    assert {row["batch_id"] for row in result.base.events} == {second.batch_id}
    assert result.children["allocations"] == ({
        "record_id": second.events[0]["record_id"], "run_id": RUN,
        "batch_id": second.batch_id},)
    assert first.events[0]["batch_id"] == first.batch_id
    assert allocation["content_hash"] == "old-seal"


def test_compound_preserves_nested_normalized_parent_chain():
    first = _base(1, 11, 0)
    second = _base(2, 12, 11,
                   kind=("order_management", "protection_reconciliation"))
    parent = {"record_id": second.events[0]["record_id"], "run_id": RUN,
              "batch_id": second.batch_id}
    action = {"record_id": str(UUID(int=303)), "parent_record_id": parent["record_id"],
              "run_id": RUN, "batch_id": second.batch_id}
    reply = {"record_id": str(UUID(int=304)), "parent_record_id": action["record_id"],
             "run_id": RUN, "batch_id": second.batch_id}
    unit = V4ProtectionReconciliationBatch(second, parent, (action,), (reply,))

    result = coalesce_v4_units((first, unit))
    assert result.children["reconciliation_replies"][0]["parent_record_id"] == action["record_id"]
    assert all(row["batch_id"] == result.base.batch_id
               for rows in result.children.values() for row in rows)


def test_compound_rejects_gap_and_foreign_parent():
    first = _base(1, 11, 0)
    gap = _base(3, 12, 11)
    with pytest.raises(ValueError, match="adjacent"):
        coalesce_v4_units((first, gap))
    second = _base(2, 12, 11,
                   kind=("portfolio_management", "portfolio_allocation"))
    foreign = {"record_id": str(UUID(int=999)), "run_id": RUN,
               "batch_id": second.batch_id}
    with pytest.raises(ValueError, match="parent"):
        coalesce_v4_units((first, V4PortfolioAllocationBatch(second, foreign)))


def test_compound_accepts_1024_event_transport_bound_but_rejects_1025():
    units = tuple(_base(sequence, 10 + sequence,
                        0 if sequence == 1 else 9 + sequence)
                  for sequence in range(1, 1026))
    compound = coalesce_v4_units(units[:1024], max_events=1024)
    assert len(compound.base.events) == 1024
    with pytest.raises(ValueError, match="event bound"):
        coalesce_v4_units(units, max_events=1024)


def test_mixed_preparation_seals_combined_base_without_clickhouse_io():
    first = batch()
    second_id = str(UUID(int=405))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=406)), "batch_id": second_id,
        "sequence": 2,
    })
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    compound = coalesce_v4_units((first, second))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("V4 compound preparation must not call ClickHouse")

    client = SimpleNamespace(
        typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()),
        execute=forbidden)
    base, families = prepare_compound_v4_families(client, compound)
    assert len(dict(base)["trading_event_v1"]) == 2
    assert dict(families)["trading_event_v1"] == dict(base)["trading_event_v1"]
    assert all(row["batch_id"] == second_id
               for _, rows in families for row in rows)


def test_mixed_preparation_retains_broker_acknowledgement_child():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    source = JournalRecord(
        str(UUID(int=181)), "run-ack-compound", 1, at, at,
        "broker", "order_acknowledgement", "1001", "DU1",
        {"order_id": "1001", "order_status": "Submitted",
         "local_order_id": "coid-1", "order_group_id": "group-1",
         "decision_to_submit_ms": 1.25, "ticker": "AAA",
         "action": "enter_long", "intent_id": "intent-1",
         "correlation_id": "correlation-1", "causation_id": "causation-1",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1})
    first = broker_acknowledgement_batch_v4(
        source, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=str(UUID(int=183)), prior_batch_id=NIL,
        source_cursor="2026-08-18:31000")
    second_id = str(UUID(int=184))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.base.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=185)), "batch_id": second_id,
        "sequence": 2, "category": "run_state", "entity_type": "lifecycle",
    })
    second = replace(first.base, batch_id=second_id,
                     prior_batch_id=first.base.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    compound = coalesce_v4_units((first, second))
    client = SimpleNamespace(
        typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()),
        execute=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("pure preparation queried ClickHouse")))
    _, families = prepare_compound_v4_families(client, compound)
    ack, = dict(families)[ACKNOWLEDGEMENT.name]
    assert ack["record_id"] == source.record_id
    assert ack["batch_id"] == second_id


def test_compound_publishes_one_cold_verified_commit_for_two_events():
    first = batch()
    second_id = str(UUID(int=407))
    second_event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.events[0].items()
           if key != "content_hash"},
        "record_id": str(UUID(int=408)), "batch_id": second_id,
        "sequence": 2,
    })
    second = replace(first, batch_id=second_id, prior_batch_id=first.batch_id,
                     first_sequence=2, last_sequence=2, events=(second_event,))
    client = attached_v4_client()
    assert publish_compound_v4(client, coalesce_v4_units((first, second))) == second_id
    prefix = load_verified_v4_prefix(client, first.run_id)
    assert prefix.last_batch_id == second_id
    assert prefix.last_sequence == 2


def test_compound_can_cold_verify_intent_and_its_causal_oms_consumer():
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    source_intent = intent()
    first = strategy_intent_batch(
        source_intent, run_id=RUN, run_month=date(2026, 8, 1),
        account_id="DU1", attempt_id=ATTEMPT, batch_id=str(UUID(int=411)),
        prior_batch_id=NIL, sequence=1, source_cursor="start",
        run_status="running", recorded_at=at)
    order = OrderRequest(acctId="DU1", conid=123, cOID="entry-1",
                         ticker="test", orderType="LMT", side="BUY",
                         quantity=5, price=12.5)
    group = _ManagedOrderGroup(
        "group-1", source_intent, "DU1", StrategyOrderPlan((order,)),
        OrderManagementState.CREATED, at, at, [order], remaining_quantity=5.)
    group.tactic = ExecutionTactic(
        ExecutionUrgency.URGENT, "BUY", (PriceStep(0, 12.5), PriceStep(200, 12.6)),
        ExecutionQuote(12.4, 12.6, at, 0.01), 200)
    second = oms_group_state_batch(
        group, run_id=RUN, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=str(UUID(int=412)),
        prior_batch_id=first.batch_id, sequence=2, source_cursor="start",
        run_status="running", strategy_id="strategy-1", strategy_revision=1,
        recorded_at=at, published_intent_batch=first,
        committed_intent_batch_id=first.batch_id)
    tactic_state, tactic_steps = tactic_rows(
        group.tactic, group_record_id=second.events[0]["record_id"],
        run_id=RUN, event_month="2026-08-01", batch_id=second.batch_id,
        account_id="DU1")
    second_unit = V4OmsTacticBatch(second, tactic_state, tactic_steps)
    client = attached_v4_client()
    compound = coalesce_v4_units((first, second_unit))
    assert _coalesce_v4_units((first, second_unit)) == (compound,)
    assert publish_compound_v4(client, compound) == second.batch_id
    prefix = load_verified_v4_prefix(client, RUN)
    assert prefix.last_sequence == 2
    assert prefix.batch_ids == (second.batch_id,)
    assert len(client.tables["trading_oms_execution_tactic_v1"]) == 1
    assert len(client.tables["trading_oms_execution_step_v1"]) == 2
    recovered, = load_committed_oms_group_state_page(
        client, prefix, require_tactic=True)
    assert recovered.tactic == group.tactic and recovered.tactic_recorded
    committed_source = _committed_intent_source(
        compound.base, first.events[0]["record_id"])
    assert committed_source.batch_id == prefix.last_batch_id
    assert committed_source.events[0]["batch_id"] == prefix.last_batch_id
    later = oms_group_state_batch(
        group, run_id=RUN, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=str(UUID(int=413)),
        prior_batch_id=second.batch_id, sequence=3, source_cursor="start",
        run_status="running", strategy_id="strategy-1", strategy_revision=1,
        recorded_at=at, published_intent_batch=committed_source,
        committed_intent_batch_id=second.batch_id)
    assert later.intent_uses[0]["intent_record_id"] == first.events[0]["record_id"]
