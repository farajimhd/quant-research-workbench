"""Normalized broker submission acknowledgement shared by Backtest and Live.

Project on the bounded journal lane. Broker-specific fields require a new
versioned contract; neither runtime may persist an opaque response fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any
from uuid import UUID

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord


ACKNOWLEDGEMENT = TableContract(
    "trading_broker_acknowledgement_v4",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("order_group_id", "String"), ("broker_order_id", "String"),
     ("local_order_id", "String"),
     ("order_status", "LowCardinality(String)"),
     ("decision_to_submit_ms", "Nullable(Decimal(38, 10))"),
     ("ticker", "LowCardinality(String)"), ("action", "String"),
     ("intent_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,order_group_id,record_id",
)


@dataclass(frozen=True, slots=True)
class BrokerAcknowledgementProjection:
    event: dict[str, Any]
    detail: dict[str, Any]


def _duration(value: object) -> str | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError("Broker acknowledgement duration is not numeric")
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError("Broker acknowledgement duration is invalid")
        return str(number.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_EVEN))
    except InvalidOperation as exc:
        raise ValueError("Broker acknowledgement duration exceeds its column") from exc


def project_broker_acknowledgement_v4(
    record: JournalRecord, *, batch_id: str,
) -> BrokerAcknowledgementProjection:
    """Accept precisely the simulated submission reply and OMS scalar lineage."""
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(batch_id)
    fields = {
        "order_id", "order_status", "local_order_id", "order_group_id",
        "decision_to_submit_ms", "ticker", "action", "intent_id",
        "correlation_id", "causation_id", "strategy_id", "strategy_revision",
    }
    payload = record.payload
    if ((record.category, record.entity_type) != ("broker", "order_acknowledgement")
            or not record.run_id or not record.account_id
            or not isinstance(payload, dict) or set(payload) != fields
            or record.event_time.tzinfo is None or record.recorded_at.tzinfo is None):
        raise ValueError("Broker acknowledgement has an unmodeled source")
    strings = fields - {"decision_to_submit_ms", "strategy_revision"}
    if (any(type(payload[key]) is not str or not payload[key] for key in strings)
            or payload["ticker"] != payload["ticker"].upper()
            or payload["order_id"] != record.entity_id
            or type(payload["strategy_revision"]) is not int
            or not 0 <= payload["strategy_revision"] <= 0xFFFFFFFF
            or payload["order_status"] not in {"Submitted", "Inactive"}):
        raise ValueError("Broker acknowledgement identity or status is invalid")
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1).isoformat()
    event = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "sequence": record.sequence, "account_id": record.account_id,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "correlation_id": payload["correlation_id"],
        "causation_id": payload["causation_id"],
    }
    detail = typed_row(ACKNOWLEDGEMENT.name, {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "order_group_id": payload["order_group_id"],
        "broker_order_id": payload["order_id"],
        "local_order_id": payload["local_order_id"],
        "order_status": payload["order_status"],
        "decision_to_submit_ms": _duration(payload["decision_to_submit_ms"]),
        "ticker": payload["ticker"], "action": payload["action"],
        "intent_id": payload["intent_id"],
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
    })
    return BrokerAcknowledgementProjection(event, detail)
