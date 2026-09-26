"""Normalized Strategy 1 protection-replacement failure on the V4 journal.

The failed amendment is joined to its already published typed intent. No
exception object, broker response, or opaque payload is persisted.
"""
from __future__ import annotations

from datetime import date, timezone
from typing import Any
from uuid import UUID

from .journal_contract import JournalRecord
from .signals import StrategyIntent
from .strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def protection_deferral_batch_v4(
    record: JournalRecord, *, source_batch: Any, source_intent: StrategyIntent,
    run_month: date, attempt_id: str, batch_id: str,
    prior_batch_id: str, source_cursor: str,
    strategy_id: str, strategy_revision: int,
):
    """Project exactly one failed amendment into the existing decision family."""
    from .arte_journal_writer import TypedJournalBatch

    for identity in (record.record_id, attempt_id, batch_id, prior_batch_id):
        UUID(str(identity))
    payload = record.payload
    allowed = {"action", "reason", "correlation_id", "causation_id"}
    if ((record.category, record.entity_type) !=
            ("order_management", "protection_replacement_deferred")
            or not isinstance(payload, dict) or set(payload) - allowed
            or not {"action", "reason"} <= set(payload)
            or payload["action"] not in {"replace_profit_target", "replace_protective_stop"}
            or type(payload["reason"]) is not str
            or record.entity_id != source_intent.intent_id
            or record.event_time != source_intent.event_time
            or not record.account_id or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None
            or source_intent.action != payload["action"]
            or not source_batch.intents or len(source_batch.intents) != 1
            or source_batch.intents[0]["intent_id"] != source_intent.intent_id
            or source_batch.intents[0]["account_id"] != record.account_id
            or source_batch.intents[0]["ticker"] != source_intent.ticker
            or source_batch.intents[0]["action"] != source_intent.action
            or source_batch.run_id != record.run_id
            or strategy_id != STRATEGY_ID
            or strategy_revision != STRATEGY_NUMBER):
        raise ValueError("Strategy 1 protection deferral lacks its typed intent")
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1)
    if run_month != month:
        raise ValueError("Strategy 1 protection deferral differs from run month")
    at = record.event_time.astimezone(timezone.utc).isoformat()
    event = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month.isoformat(), "attempt_id": attempt_id,
        "batch_id": batch_id, "sequence": record.sequence,
        "account_id": record.account_id, "event_time": at,
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    detail = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month.isoformat(), "batch_id": batch_id,
        "account_id": record.account_id, "intent_id": source_intent.intent_id,
        "ticker": source_intent.ticker,
        "decision_kind": "protection_replacement_deferred",
        "action": "wait", "reason_code": "broker_replacement_not_confirmed",
        "reason_detail": payload["reason"], "reference_price": None,
        "strategy_id": STRATEGY_ID, "strategy_revision": STRATEGY_NUMBER,
        "assignment_status": "", "reason_count": 0,
        "source_event_time": at,
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        intent_decisions=(detail,),
    )
