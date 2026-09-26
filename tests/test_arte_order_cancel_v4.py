"""Exact scalar projection of Strategy 1 cancellation commands and replies."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_order_cancel_v4 import CANCEL, project_order_cancel_v4
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
LINEAGE = {"strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
           "correlation_id": "correlation", "causation_id": "causation"}


def record(category, entity_type, payload):
    return JournalRecord(str(uuid4()), "run-1", 1, AT, AT, category,
                         entity_type, "42", "DU1", {**LINEAGE, **payload})


@pytest.mark.parametrize(("category", "entity_type", "payload", "result"), [
    ("command", "order_cancel",
     {"reason": "replace_strategy_protection", "ticker": "AAA"}, "command"),
    ("broker", "order_cancel_requested",
     {"msg": "Request was submitted", "order_id": 42,
      "conid": 123, "account": "DU1"}, "submitted"),
    ("broker", "order_cancel_requested",
     {"msg": "Order was already terminal during protection replacement",
      "order_id": "42", "status": "not_found"}, "replacement_terminal"),
    ("broker", "order_cancel_requested",
     {"order_group_id": "group-1", "reason": "entry_closed",
      "broker_response": {"already_terminal": "Filled"},
      "ticker": "AAA", "action": "enter_long", "intent_id": "intent-1"},
     "already_terminal"),
])
def test_cancel_projection_is_tabular(category, entity_type, payload, result):
    event, detail = project_order_cancel_v4(
        record(category, entity_type, payload),
        attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert event["entity_type"] == entity_type
    assert detail["result_kind"] == result
    assert set(detail) == {name for name, _ in CANCEL.columns}
    assert all(not isinstance(value, (dict, list, tuple, bytes))
               for value in detail.values())


def test_cancel_projection_rejects_opaque_reply():
    source = record("broker", "order_cancel_requested", {
        "order_group_id": "group-1", "reason": "entry_closed",
        "broker_response": {"already_terminal": "Filled", "extra": {"x": 1}},
        "ticker": "AAA", "action": "enter_long", "intent_id": "intent-1",
    })
    with pytest.raises(ValueError, match="closed simulated shape"):
        project_order_cancel_v4(source, attempt_id=str(uuid4()),
                                batch_id=str(uuid4()))
