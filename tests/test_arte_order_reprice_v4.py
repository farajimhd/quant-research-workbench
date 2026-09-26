"""Closed scalar projection for causal Strategy 1 adaptive repricing."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_order_reprice_v4 import REPRICE, project_order_reprice_v4
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
COMMON = {
    "order_group_id": "group-1", "requested_price": 10.25,
    "ticker": "AAA", "action": "enter_long", "intent_id": "intent-1",
    "correlation_id": "correlation", "causation_id": "causation",
    "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
}


def record(entity_type, payload):
    return JournalRecord(str(uuid4()), "run-1", 1, AT, AT,
                         "broker", entity_type, "42", "DU1", payload)


@pytest.mark.parametrize(("entity_type", "extra", "result"), [
    ("order_repriced", {
        "remaining_quantity": 10., "quote_observed_at": AT.isoformat(),
        "quote_bid": 10.2, "quote_ask": 10.25,
        "broker_response": [{"order_id": "42", "order_status": "Submitted",
                             "local_order_id": "entry-42"}],
    }, "modified"),
    ("order_reprice_error", {"error": "broker rejected modification"}, "error"),
])
def test_repricing_projection_is_scalar(entity_type, extra, result):
    event, detail = project_order_reprice_v4(
        record(entity_type, {**COMMON, **extra}),
        attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert event["entity_type"] == entity_type
    assert detail["result_kind"] == result
    assert set(detail) == {name for name, _ in REPRICE.columns}
    assert all(not isinstance(value, (dict, list, tuple, bytes))
               for value in detail.values())


def test_repricing_rejects_future_quote_and_opaque_reply():
    source = {
        **COMMON, "remaining_quantity": 10.,
        "quote_observed_at": "2026-08-18T12:01:00.000001+00:00",
        "quote_bid": 10.2, "quote_ask": 10.25,
        "broker_response": [{"order_id": "42", "order_status": "Submitted",
                             "local_order_id": "entry-42"}],
    }
    with pytest.raises(ValueError, match="unavailable at event time"):
        project_order_reprice_v4(record("order_repriced", source),
                                 attempt_id=str(uuid4()), batch_id=str(uuid4()))
    source["quote_observed_at"] = AT.isoformat()
    source["broker_response"][0]["opaque"] = {"x": 1}
    with pytest.raises(ValueError, match="exact simulated acknowledgement"):
        project_order_reprice_v4(record("order_repriced", source),
                                 attempt_id=str(uuid4()), batch_id=str(uuid4()))
