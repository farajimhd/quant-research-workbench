"""Staged scalar reply contract; V4 Backtest admission remains unchanged."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.trading_runtime.arte_broker_acknowledgement_v5 import (
    ACKNOWLEDGEMENT_V5, project_broker_acknowledgement_v5,
)
from src.trading_runtime.arte_journal_writer import (
    _canonical_typed_content, v4_storage_contracts,
)
from src.trading_runtime.journal_contract import JournalRecord


def _record():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    return JournalRecord(str(UUID(int=1)), "run-1", 7, at, at,
                         "broker", "order_acknowledgement", "1001", "DU1", {})


def _project(record=None, **changes):
    arguments = dict(
        record=record or _record(), provider="ibkr_cpapi",
        client_order_id="coid-1", order_group_id="group-1",
        intent_id="intent-1",
        response={"order_id": "1001", "order_status": "PreSubmitted",
                  "encrypt_message": "1"},
        batch_id=str(UUID(int=2)), decision_to_submit_ms=1.234567890123,
    )
    arguments.update(changes)
    return project_broker_acknowledgement_v5(**arguments)


def test_ibkr_reply_without_local_id_has_exact_scalar_contract():
    row = _project()
    assert row["client_order_id"] == "coid-1"
    assert row["order_status"] == "PreSubmitted"
    assert row["encrypt_message"] == 1
    assert row["decision_to_submit_ms"] == "1.2345678901"
    assert row["event_month"] == "2026-08-01"
    assert _canonical_typed_content(
        ACKNOWLEDGEMENT_V5.name,
        {key: value for key, value in row.items() if key != "content_hash"},
    )["broker_order_id"] == "1001"
    ddl = ACKNOWLEDGEMENT_V5.ddl()
    assert "JSON" not in ddl and "storage_policy = 'live_market_ssd'" in ddl
    assert "PARTITION BY toYYYYMM(event_month)" in ddl
    assert ACKNOWLEDGEMENT_V5.name not in {
        table.name for table in v4_storage_contracts()
    }


def test_simulated_reply_requires_matching_client_identity():
    row = _project(provider="simulated", response={
        "order_id": "1001", "order_status": "Submitted",
        "local_order_id": "coid-1"})
    assert row["encrypt_message"] is None


@pytest.mark.parametrize("change", [
    {"response": {"order_id": "1001", "order_status": "Submitted",
                  "warning": "confirm"}},
    {"response": {"order_id": "1001", "order_status": "Submitted",
                  "local_order_id": "wrong"}},
    {"response": {"order_id": "other", "order_status": "Submitted"}},
    {"response": {"order_id": "1001", "order_status": "Submitted",
                  "encrypt_message": "maybe"}},
    {"decision_to_submit_ms": float("inf")},
    {"decision_to_submit_ms": -1},
])
def test_unmodeled_or_inconsistent_reply_fails_closed(change):
    with pytest.raises(ValueError):
        _project(**change)


def test_wrong_journal_family_fails_closed():
    with pytest.raises(ValueError):
        _project(replace(_record(), category="strategy"))
