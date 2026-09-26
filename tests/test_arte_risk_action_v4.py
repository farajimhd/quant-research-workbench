"""Exact parent/reply risk-action projection without opaque persistence."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_risk_action_v4 import (
    ACTION, REPLY, project_risk_action_v4,
)
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
LINEAGE = {"reason": "risk_limit", "ticker": "AAA",
           "correlation_id": "correlation", "causation_id": "causation",
           "strategy_id": "early-squeeze-strategy", "strategy_revision": 1}


def record(kind, payload, *, entity_id="42"):
    return JournalRecord(str(uuid4()), "run-1", 1, AT, AT, "risk", kind,
                         entity_id, "DU1", {**LINEAGE, **payload})


def test_kill_entry_is_one_typed_reply():
    event, parent, replies = project_risk_action_v4(record(
        "kill_entry_order", {
            "order_group_id": "group-1", "action": "enter_long",
            "intent_id": "intent-1",
            "broker_response": {"msg": "Request was submitted",
                                "order_id": 42, "conid": 123, "account": "DU1"},
        }), attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert event["entity_type"] == "kill_entry_order"
    assert parent["reply_count"] == 1
    assert replies[0]["reply_kind"] == "cancel_submitted"
    assert set(parent) == {name for name, _ in ACTION.columns}
    assert set(replies[0]) == {name for name, _ in REPLY.columns}


def test_emergency_flatten_requires_two_exact_order_replies():
    prefix = "risk-flatten-test"
    payload = {
        "quantity": 10., "limit_price": 9.98, "fallback_stop": 9.8,
        "broker_response": [
            {"order_id": "42", "order_status": "Submitted",
             "local_order_id": f"{prefix}-limit"},
            {"order_id": "43", "order_status": "Submitted",
             "local_order_id": f"{prefix}-stop"},
        ],
    }
    _, parent, replies = project_risk_action_v4(
        record("emergency_flatten", payload, entity_id=prefix),
        attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert parent["reply_count"] == 2
    assert [reply["ordinal"] for reply in replies] == [0, 1]
    assert all(reply["reply_kind"] == "placement" for reply in replies)
    payload["broker_response"][1]["opaque"] = {"x": 1}
    with pytest.raises(ValueError, match="unmodeled broker reply"):
        project_risk_action_v4(
            record("emergency_flatten", payload, entity_id=prefix),
            attempt_id=str(uuid4()), batch_id=str(uuid4()))
