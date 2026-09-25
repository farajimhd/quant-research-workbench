from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.backend.backtest_protection_change_v3 import (
    CHANGE, ENTRY_ORDER, project_protection_change_v3,
    recover_protection_change_payload, seal_protection_changes_v3,
)
from src.trading_runtime.journal_contract import JournalRecord


def _record(*, entry_order_ids=None, price=5.75) -> JournalRecord:
    at = datetime(2025, 8, 18, 12, 0, tzinfo=timezone.utc)
    return JournalRecord(
        str(uuid4()), "run-1", 7, at, at, "protection", "protection_change",
        "broker-1", "account-1", {
            "schema_version": 1, "order_group_id": "group-1",
            "entry_order_ids": ["entry-1", "entry-2"] if entry_order_ids is None
            else entry_order_ids,
            "order_id": "broker-1", "client_order_id": "client-1",
            "kind": "stop", "phase": "effective", "price": price,
            "active": True, "ticker": "ABC", "source_intent_id": "intent-1",
            "correlation_id": "correlation-1", "causation_id": "causation-1",
        },
    )


def test_protection_change_is_normalized_and_exactly_recoverable():
    record = _record()
    batch = str(uuid4())
    result = project_protection_change_v3(record, attempt_id=str(uuid4()), batch_id=batch)
    assert set(result.detail) == {name for name, _ in CHANGE.columns}
    assert all(set(row) == {name for name, _ in ENTRY_ORDER.columns}
               for row in result.entry_orders)
    assert result.detail["price"] == "5.750000000000000000"
    assert [row["entry_order_id"] for row in result.entry_orders] == ["entry-1", "entry-2"]
    assert recover_protection_change_payload(
        result.event, result.detail, result.entry_orders) == record.payload
    assert seal_protection_changes_v3(
        [result.detail], result.entry_orders, [result.event],
        run_id=record.run_id, batch_id=batch)["protection_change_count"] == 1


def test_protection_change_rejects_missing_or_tampered_children():
    record = _record()
    batch = str(uuid4())
    result = project_protection_change_v3(record, attempt_id=str(uuid4()), batch_id=batch)
    for children in (
        result.entry_orders[:-1],
        tuple(reversed(result.entry_orders)),
        ({**result.entry_orders[0], "entry_order_id": "different"},
         result.entry_orders[1]),
    ):
        with pytest.raises(ValueError):
            seal_protection_changes_v3(
                [result.detail], children, [result.event],
                run_id=record.run_id, batch_id=batch)
    with pytest.raises(ValueError, match="content"):
        seal_protection_changes_v3(
            [{**result.detail, "price": "6.000000000000000000"}],
            result.entry_orders, [result.event], run_id=record.run_id, batch_id=batch)


def test_protection_change_rejects_unmodeled_or_lossy_source():
    record = _record()
    for payload in (
        {**record.payload, "unknown": 1},
        {**record.payload, "entry_order_ids": ["entry-2", "entry-1"]},
        {**record.payload, "price": 1e-19},
        {**record.payload, "active": 1},
    ):
        with pytest.raises(ValueError):
            project_protection_change_v3(
                replace(record, payload=payload), attempt_id=str(uuid4()),
                batch_id=str(uuid4()))
    empty = _record(entry_order_ids=[])
    result = project_protection_change_v3(
        empty, attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert result.entry_orders == ()
    assert recover_protection_change_payload(result.event, result.detail, ()) == empty.payload
