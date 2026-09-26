"""A failed Strategy 1 protection amendment retains normalized intent lineage."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.trading_runtime.arte_protection_deferral_v4 import (
    protection_deferral_batch_v4,
)
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.signals import StrategyIntent
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def _source():
    at = datetime(2026, 8, 18, 12, 1, tzinfo=timezone.utc)
    intent = StrategyIntent(
        intent_id="protection-1", ticker="AAA", event_time=at,
        action="replace_protective_stop", quantity=12., reference_price=10.,
        invalidation_price=9.5, metadata={})
    source = SimpleNamespace(run_id="run-1", intents=({
        "intent_id": intent.intent_id, "account_id": "DU1",
        "ticker": intent.ticker, "action": intent.action,
    },))
    record = JournalRecord(
        str(uuid4()), "run-1", 2, at, at,
        "order_management", "protection_replacement_deferred",
        intent.intent_id, "DU1",
        {"action": intent.action, "reason": "broker rejected amendment"})
    return intent, source, record


def _project(intent, source, record):
    return protection_deferral_batch_v4(
        record, source_batch=source, source_intent=intent,
        run_month=date(2026, 8, 1), attempt_id=str(uuid4()),
        batch_id=str(uuid4()), prior_batch_id=str(uuid4()),
        source_cursor="boundary:100ms:1", strategy_id=STRATEGY_ID,
        strategy_revision=STRATEGY_NUMBER)


def test_failed_protection_uses_existing_typed_intent_decision_family():
    intent, source, record = _source()
    batch = _project(intent, source, record)
    assert batch.events[0]["entity_id"] == intent.intent_id
    assert batch.intent_decisions[0]["decision_kind"] == "protection_replacement_deferred"
    assert batch.intent_decisions[0]["reason_detail"] == "broker rejected amendment"
    assert batch.intent_decisions[0]["ticker"] == "AAA"
    assert batch.intent_decisions[0]["reason_count"] == 0


def test_failed_protection_rejects_wrong_source_or_unmodeled_payload():
    intent, source, record = _source()
    with pytest.raises(ValueError, match="typed intent"):
        _project(replace(intent, intent_id="other"), source, record)
    with pytest.raises(ValueError, match="typed intent"):
        _project(intent, source, replace(record, payload={
            **record.payload, "broker_response": {"opaque": True}}))
