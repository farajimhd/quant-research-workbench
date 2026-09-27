"""Pure V4 compound preparation before any ClickHouse or Keeper mutation."""
from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_compound_v4 import (
    coalesce_v4_units, prepare_compound_v4_families,
)
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, typed_row
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from src.trading_runtime.arte_broker_acknowledgement_v4 import (
    ACKNOWLEDGEMENT, broker_acknowledgement_batch_v4,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)
from tests.test_arte_journal_writer import batch


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
