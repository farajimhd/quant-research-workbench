"""Scalar, causal adaptive-order repricing evidence for Strategy 1.

The successful simulated reply is a single exact acknowledgement. Failures
retain error text and requested price, not an opaque exception or JSON blob.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord


REPRICE = TableContract(
    "trading_order_reprice_v4",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("order_group_id", "String"), ("broker_order_id", "String"),
     ("result_kind", "LowCardinality(String)"),
     ("requested_price", "Decimal(38, 10)"),
     ("remaining_quantity", "Nullable(Decimal(38, 10))"),
     ("quote_observed_at", "Nullable(DateTime64(6, 'UTC'))"),
     ("quote_bid", "Nullable(Decimal(38, 10))"),
     ("quote_ask", "Nullable(Decimal(38, 10))"),
     ("broker_order_status", "String"), ("local_order_id", "String"),
     ("error_text", "String"), ("ticker", "LowCardinality(String)"),
     ("intent_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,order_group_id,record_id",
)


def _number(value: object, *, nonnegative: bool = False) -> str:
    if type(value) not in (int, float, Decimal):
        raise ValueError("Repricing scalar must be numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Repricing scalar is invalid") from exc
    if not number.is_finite() or number <= 0 and not (nonnegative and number == 0):
        raise ValueError("Repricing scalar is nonpositive or nonfinite")
    if number.as_tuple().exponent < -10:
        raise ValueError("Repricing scalar exceeds decimal precision")
    if number >= Decimal(10) ** 28:
        raise ValueError("Repricing scalar exceeds decimal range")
    return str(number)


def project_order_reprice_v4(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    kind = (record.category, record.entity_type)
    if (kind not in {("broker", "order_repriced"),
                     ("broker", "order_reprice_error")}
            or not record.run_id or not record.account_id or not record.entity_id
            or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None
            or not isinstance(record.payload, dict)):
        raise ValueError("Repricing event envelope is invalid")
    payload = record.payload
    common = {"order_group_id", "requested_price", "ticker", "action",
              "intent_id", "correlation_id", "causation_id",
              "strategy_id", "strategy_revision"}
    expected = (common | {"remaining_quantity", "quote_observed_at",
                          "quote_bid", "quote_ask", "broker_response"}
                if kind[1] == "order_repriced" else common | {"error"})
    if (set(payload) != expected
            or any(type(payload[key]) is not str or not payload[key]
                   for key in ("order_group_id", "ticker", "action", "intent_id",
                               "correlation_id", "causation_id", "strategy_id"))
            or payload["ticker"] != payload["ticker"].upper()
            or type(payload["strategy_revision"]) is not int
            or not 0 < payload["strategy_revision"] <= 0xFFFFFFFF):
        raise ValueError("Repricing event fields or lineage are invalid")
    requested = _number(payload["requested_price"])
    remaining = observed = bid = ask = None
    order_status = local_order_id = error_text = ""
    if kind[1] == "order_repriced":
        remaining = _number(payload["remaining_quantity"], nonnegative=True)
        bid, ask = _number(payload["quote_bid"]), _number(payload["quote_ask"])
        if Decimal(bid) > Decimal(ask):
            raise ValueError("Repricing quote is crossed")
        if type(payload["quote_observed_at"]) is not str:
            raise ValueError("Repricing quote timestamp is invalid")
        try:
            quote_time = datetime.fromisoformat(payload["quote_observed_at"])
        except ValueError as exc:
            raise ValueError("Repricing quote timestamp is invalid") from exc
        if (quote_time.tzinfo is None
                or quote_time.astimezone(timezone.utc) >
                   record.event_time.astimezone(timezone.utc)):
            raise ValueError("Repricing quote is unavailable at event time")
        observed = quote_time.astimezone(timezone.utc).isoformat()
        replies = payload["broker_response"]
        if (type(replies) is not list or len(replies) != 1
                or type(replies[0]) is not dict
                or set(replies[0]) != {"order_id", "order_status", "local_order_id"}
                or str(replies[0]["order_id"]) != record.entity_id
                or replies[0]["order_status"] not in {"Submitted", "Inactive"}
                or type(replies[0]["local_order_id"]) is not str
                or not replies[0]["local_order_id"]):
            raise ValueError("Repricing lacks an exact simulated acknowledgement")
        order_status = replies[0]["order_status"]
        local_order_id = replies[0]["local_order_id"]
    else:
        if (type(payload["error"]) is not str or not payload["error"]
                or len(payload["error"]) > 1024):
            raise ValueError("Repricing error text is invalid")
        error_text = payload["error"]
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1).isoformat()
    event = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "attempt_id": attempt_id, "batch_id": batch_id,
        "sequence": record.sequence, "account_id": record.account_id,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "correlation_id": payload["correlation_id"],
        "causation_id": payload["causation_id"],
    }
    detail = typed_row(REPRICE.name, {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "order_group_id": payload["order_group_id"],
        "broker_order_id": record.entity_id,
        "result_kind": "modified" if kind[1] == "order_repriced" else "error",
        "requested_price": requested, "remaining_quantity": remaining,
        "quote_observed_at": observed, "quote_bid": bid, "quote_ask": ask,
        "broker_order_status": order_status, "local_order_id": local_order_id,
        "error_text": error_text, "ticker": payload["ticker"],
        "intent_id": payload["intent_id"],
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
    })
    return event, detail


def order_reprice_batch_v4(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
):
    from .arte_journal_writer import TypedJournalBatch, V4OrderRepriceBatch

    event, detail = project_order_reprice_v4(
        record, attempt_id=attempt_id, batch_id=batch_id)
    if run_month.day != 1:
        raise ValueError("Run month must start on day one")
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
    )
    return V4OrderRepriceBatch(base, detail)
