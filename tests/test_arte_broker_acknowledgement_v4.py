"""Exact, scalar broker submission replies for the opt-in V4 journal."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.trading_runtime.arte_broker_acknowledgement_v4 import (
    ACKNOWLEDGEMENT, project_broker_acknowledgement_v4,
)
from src.trading_runtime.arte_journal_writer import _canonical_typed_content
from src.trading_runtime.journal_contract import JournalRecord


def _record():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    return JournalRecord(
        str(UUID(int=1)), "run-1", 7, at, at,
        "broker", "order_acknowledgement", "1001", "DU1",
        {
            "order_id": "1001", "order_status": "Submitted",
            "local_order_id": "coid-1", "order_group_id": "group-1",
            "decision_to_submit_ms": 1.234567890123,
            "ticker": "AAA", "action": "enter_long", "intent_id": "intent-1",
            "correlation_id": "correlation-1", "causation_id": "causation-1",
            "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
        },
    )


def test_acknowledgement_is_normalized_and_exactly_sealed():
    projected = project_broker_acknowledgement_v4(
        _record(), batch_id=str(UUID(int=2)))
    assert projected.event["entity_id"] == projected.detail["broker_order_id"]
    assert projected.detail["decision_to_submit_ms"] == "1.2345678901"
    assert projected.detail["event_month"] == "2026-08-01"
    assert projected.detail["content_hash"]
    assert "JSON" not in ACKNOWLEDGEMENT.ddl()
    assert "storage_policy = 'live_market_ssd'" in ACKNOWLEDGEMENT.ddl()
    assert _canonical_typed_content(
        ACKNOWLEDGEMENT.name,
        {key: value for key, value in projected.detail.items()
         if key != "content_hash"},
    )["broker_order_id"] == "1001"


@pytest.mark.parametrize("change", [
    {"order_id": "other"}, {"unexpected": "opaque"},
    {"order_status": "unknown"}, {"ticker": "aaa"},
    {"decision_to_submit_ms": float("inf")},
])
def test_acknowledgement_rejects_unmodelled_or_inconsistent_reply(change):
    record = _record()
    with pytest.raises(ValueError):
        project_broker_acknowledgement_v4(
            replace(record, payload={**record.payload, **change}),
            batch_id=str(UUID(int=2)))
