"""Strategy 1 modifications remain scalar and separate from placements."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.trading_runtime.arte_order_modify_command_v1 import (
    MODIFY_COMMAND, project_order_modify_command_v1,
)
from src.trading_runtime.ibkr_schema import OrderRequest
from src.trading_runtime.journal_contract import JournalRecord


AT = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)


def _inputs():
    request = OrderRequest(
        acctId="DU1", conid=123, cOID="client-1", ticker="TEST",
        orderType="LMT", side="SELL", quantity=5, price=12.34,
        outsideRTH=True)
    record = JournalRecord(
        str(uuid4()), "live:DU1", 3, AT, AT, "command", "order_modify",
        "broker-42", "DU1", {
            "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
            "correlation_id": "correlation", "causation_id": "intent-2",
            "order_group_id": "group-1", "intent_id": "intent-2",
            "source_intent_record_id": str(uuid4()),
            "source_intent_content_hash": "a" * 64,
            "oms_group_record_id": str(uuid4()),
            "reason": "protection_trail",
        })
    return record, request


def test_modify_command_has_one_exact_flat_detail():
    record, request = _inputs()
    event, detail = project_order_modify_command_v1(
        record, request, attempt_id=str(uuid4()), batch_id=str(uuid4()))
    assert (event["category"], event["entity_type"], event["entity_id"]) == (
        "command", "order_modify", "broker-42")
    assert detail["broker_order_id"] == "broker-42"
    assert detail["limit_price"] == "12.34"
    assert detail["source_intent_content_hash"] == "a" * 64
    assert set(detail) == {name for name, _ in MODIFY_COMMAND.columns}
    assert all(not isinstance(value, (dict, list, tuple, bytes))
               for value in detail.values())


@pytest.mark.parametrize("change", [
    {"raw": {"canonical_metadata": {"x": 1}}},
    {"strategyParameters": ({"algo": "VWAP"},)},
    {"price": float("nan")},
    {"trailingAmt": -0.1},
])
def test_modify_command_rejects_opaque_or_inexact_request(change):
    record, request = _inputs()
    with pytest.raises(ValueError):
        project_order_modify_command_v1(
            record, replace(request, **change),
            attempt_id=str(uuid4()), batch_id=str(uuid4()))


def test_modify_command_rejects_unproved_source_identity():
    record, request = _inputs()
    record = replace(record, payload={
        **record.payload, "source_intent_content_hash": "not-a-hash"})
    with pytest.raises(ValueError, match="lineage"):
        project_order_modify_command_v1(
            record, request, attempt_id=str(uuid4()), batch_id=str(uuid4()))
