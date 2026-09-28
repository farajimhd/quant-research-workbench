"""Common scalar order-submission reply for simulated and IBKR brokers.

This is a staged V5 contract, not permission to admit live Strategy 1. The
existing V4 Backtest rows and their immutable commit hashes remain unchanged.
The caller must retain its command and request identity; a provider response
cannot invent the client order ID or silently discard an unfamiliar field.
"""
from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord


ACKNOWLEDGEMENT_V5 = TableContract(
    "trading_broker_acknowledgement_v5",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("order_group_id", "String"), ("intent_id", "String"),
     ("provider", "LowCardinality(String)"),
     ("broker_order_id", "String"), ("client_order_id", "String"),
     ("order_status", "LowCardinality(String)"),
     ("encrypt_message", "Nullable(UInt8)"),
     ("decision_to_submit_ms", "Nullable(Decimal(38, 10))"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,order_group_id,record_id",
)


def _duration(value: object) -> str | None:
    if value is None:
        return None
    if type(value) not in (int, float, Decimal):
        raise ValueError("Broker reply latency must be numeric")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError("Broker reply latency must be finite and nonnegative")
        return str(number.quantize(Decimal("0.0000000001"),
                                   rounding=ROUND_HALF_EVEN))
    except InvalidOperation as exc:
        raise ValueError("Broker reply latency exceeds its column") from exc


def project_broker_acknowledgement_v5(
    record: JournalRecord, *, provider: str, client_order_id: str,
    order_group_id: str, intent_id: str, response: Mapping[str, Any],
    batch_id: str, decision_to_submit_ms: float | None,
) -> dict[str, Any]:
    """Normalize one exact success reply; reject unknown provider fields.

    IBKR reply warnings/rejections are different outcomes and need their own
    typed facts before live admission. This projector never records a warning
    as an acknowledged order.
    """
    from src.trading_runtime.arte_journal_writer import typed_row

    if (not isinstance(record, JournalRecord)
            or (record.category, record.entity_type)
            != ("broker", "order_acknowledgement")
            or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None
            or not record.run_id or not record.account_id
            or provider not in {"simulated", "ibkr_cpapi"}
            or any(type(value) is not str or not value
                   for value in (client_order_id, order_group_id, intent_id))
            or not isinstance(response, Mapping)
            or not isinstance(record.payload, dict)):
        raise ValueError("Broker acknowledgement source is invalid")
    UUID(record.record_id)
    UUID(batch_id)
    if provider == "simulated":
        expected = {"order_id", "order_status", "local_order_id"}
        if set(response) != expected:
            raise ValueError("Simulated broker reply has unmodeled fields")
    else:
        allowed = {"order_id", "order_status", "encrypt_message",
                   "local_order_id"}
        if (not {"order_id", "order_status"} <= set(response)
                or set(response) - allowed):
            raise ValueError("IBKR broker reply has unmodeled fields")
    # The broker adapter and OMS may expose two references to the reply. Seal
    # only the response actually recorded by OMS; never hash a detached reply
    # that could silently disagree with the authoritative journal sequence.
    expected_payload = {**response, "order_group_id": order_group_id,
                        "decision_to_submit_ms": decision_to_submit_ms}
    if record.payload != expected_payload:
        raise ValueError("Broker reply differs from its recorded source")
    broker_id = response["order_id"]
    status = response["order_status"]
    local_id = response.get("local_order_id")
    if (type(broker_id) is not str or not broker_id
            or broker_id != record.entity_id
            or type(status) is not str or not status or len(status) > 64
            or (local_id is not None and local_id != client_order_id)):
        raise ValueError("Broker reply identity or status differs from request")
    if provider == "simulated" and local_id != client_order_id:
        raise ValueError("Simulated local order ID differs from request")
    encryption = response.get("encrypt_message")
    if encryption is not None and encryption not in ("0", "1"):
        raise ValueError("IBKR encryption marker is not a documented scalar")
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1)
    return typed_row(ACKNOWLEDGEMENT_V5.name, {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month.isoformat(), "batch_id": batch_id,
        "order_group_id": order_group_id, "intent_id": intent_id,
        "provider": provider, "broker_order_id": broker_id,
        "client_order_id": client_order_id, "order_status": status,
        "encrypt_message": (None if encryption is None else int(encryption)),
        "decision_to_submit_ms": _duration(decision_to_submit_ms),
    })


def broker_acknowledgement_batch_v5(
    record: JournalRecord, *, provider: str, client_order_id: str,
    order_group_id: str, intent_id: str, response: Mapping[str, Any],
    decision_to_submit_ms: float | None, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
    correlation_id: str, causation_id: str,
):
    """Build one live reply family without allowing a detached parent event."""
    from src.trading_runtime.arte_journal_writer import (
        TypedJournalBatch, V5BrokerAcknowledgementBatch,
    )

    UUID(attempt_id)
    UUID(prior_batch_id)
    if (run_month.day != 1 or record.sequence < 1 or not source_cursor
            or not correlation_id or not causation_id):
        raise ValueError("V5 broker reply has an invalid batch envelope")
    detail = project_broker_acknowledgement_v5(
        record, provider=provider, client_order_id=client_order_id,
        order_group_id=order_group_id, intent_id=intent_id,
        response=response, batch_id=batch_id,
        decision_to_submit_ms=decision_to_submit_ms)
    event = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": detail["event_month"], "attempt_id": attempt_id,
        "batch_id": batch_id, "sequence": record.sequence,
        "account_id": record.account_id,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id, "correlation_id": correlation_id,
        "causation_id": causation_id,
    }
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,))
    return V5BrokerAcknowledgementBatch(base, detail)
