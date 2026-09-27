from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_oms_tactic_projection import (
    seal_oms_tactic_rows, tactic_from_rows, tactic_rows,
)
from src.trading_runtime.order_management import (
    ExecutionQuote, ExecutionTactic, ExecutionUrgency, PriceStep,
)


COMMON = {
    "group_record_id": "00000000-0000-0000-0000-000000000111",
    "run_id": "run-one", "event_month": "2026-08-01",
    "batch_id": "00000000-0000-0000-0000-000000000222",
    "account_id": "account-one",
}


def test_tactic_codec_round_trips_exact_steps_and_absence():
    quote = ExecutionQuote(2.1, 2.2, datetime(2026, 8, 18, 8, 0,
                                              tzinfo=timezone.utc), 0.01)
    tactic = ExecutionTactic(
        ExecutionUrgency.URGENT, "BUY",
        (PriceStep(0, 2.15), PriceStep(200, 2.18)), quote, 200)
    parent, steps = tactic_rows(tactic, **COMMON)
    assert tactic_from_rows(parent, steps) == tactic
    assert tactic_rows(tactic, **COMMON) == (parent, steps)
    stored_parent = {**parent, "quote_observed_at": "2026-08-18 08:00:00.000000000"}
    assert tactic_from_rows(stored_parent, steps, stored_utc=True) == tactic
    absent, no_steps = tactic_rows(None, **COMMON)
    assert absent["has_tactic"] == 0
    assert tactic_from_rows(absent, no_steps) is None


def test_tactic_codec_rejects_corruption_and_missing_children():
    tactic = ExecutionTactic(
        ExecutionUrgency.PATIENT, "SELL", (PriceStep(0, 2.08),),
        ExecutionQuote(2.0, 2.1, datetime(2026, 8, 18, 8, 0,
                                           tzinfo=timezone.utc), 0.01), 0)
    parent, steps = tactic_rows(tactic, **COMMON)
    with pytest.raises(ValueError, match="step count"):
        tactic_from_rows(parent, ())
    with pytest.raises(ValueError, match="hash"):
        tactic_from_rows(parent, ({**steps[0], "price": "1.0000000000"},))
    with pytest.raises(ValueError, match="lineage"):
        from src.trading_runtime.arte_journal_writer import typed_row
        foreign = typed_row("trading_oms_execution_step_v1", {
            **{key: value for key, value in steps[0].items()
               if key != "content_hash"},
            "parent_record_id": str(uuid4()),
        })
        tactic_from_rows(parent, (foreign,))


def test_tactic_codec_rejects_unordered_or_out_of_duration_steps():
    quote = ExecutionQuote(2.0, 2.1, datetime.now(timezone.utc), 0.01)
    with pytest.raises(ValueError, match="increasing"):
        tactic_rows(ExecutionTactic(ExecutionUrgency.REGULAR, "BUY",
                                   (PriceStep(0, 2.01), PriceStep(0, 2.02)),
                                   quote, 100), **COMMON)
    with pytest.raises(ValueError, match="duration"):
        tactic_rows(ExecutionTactic(ExecutionUrgency.REGULAR, "BUY",
                                   (PriceStep(101, 2.01),), quote, 100), **COMMON)


def test_tactic_family_requires_exact_committed_group_lineage():
    parent, steps = tactic_rows(None, **COMMON)
    group = {"record_id": COMMON["group_record_id"],
             "run_id": COMMON["run_id"], "batch_id": COMMON["batch_id"],
             "event_month": COMMON["event_month"],
             "account_id": COMMON["account_id"]}
    event = {**group, "category": "order_management",
             "entity_type": "order_group_state"}
    seal_oms_tactic_rows((parent,), steps, (group,), (event,),
                         run_id=COMMON["run_id"], batch_id=COMMON["batch_id"])
    with pytest.raises(ValueError, match="count"):
        seal_oms_tactic_rows((), steps, (group,), (event,),
                             run_id=COMMON["run_id"], batch_id=COMMON["batch_id"])
    with pytest.raises(ValueError, match="group"):
        seal_oms_tactic_rows((parent,), steps, ({**group, "account_id": "other"},),
                             (event,), run_id=COMMON["run_id"],
                             batch_id=COMMON["batch_id"])
