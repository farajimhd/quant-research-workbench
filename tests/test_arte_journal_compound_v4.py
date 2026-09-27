"""Pure V4 compound preparation before any ClickHouse or Keeper mutation."""
from datetime import date
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units
from src.trading_runtime.arte_journal_writer import TypedJournalBatch
from src.trading_runtime.arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch
from src.trading_runtime.arte_protection_reconciliation_v4 import (
    V4ProtectionReconciliationBatch,
)


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
