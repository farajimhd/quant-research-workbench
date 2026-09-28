"""One normalized, exact broker modification command for Strategy 1.

The full desired OrderRequest is a new fact at each amendment. Its target
broker order is explicit; no event payload, JSON, or simulated reply is used
as a substitute for the pre-broker command.
"""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import UUID

from .arte_journal_schema import TableContract
from .ibkr_schema import OrderRequest
from .journal_contract import JournalRecord
from .strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


MODIFY_COMMAND = TableContract(
    "trading_order_modify_command_v1",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("account_id", "String"), ("broker_order_id", "String"),
     ("client_order_id", "String"), ("conid", "UInt64"),
     ("ticker", "LowCardinality(String)"),
     ("side", "LowCardinality(String)"),
     ("order_type", "LowCardinality(String)"),
     ("time_in_force", "LowCardinality(String)"),
     ("quantity", "Nullable(Decimal(38, 10))"),
     ("cash_quantity", "Nullable(Decimal(38, 10))"),
     ("limit_price", "Nullable(Decimal(38, 10))"),
     ("aux_price", "Nullable(Decimal(38, 10))"),
     ("outside_rth", "UInt8"),
     ("security_type", "LowCardinality(String)"),
     ("listing_exchange", "LowCardinality(String)"),
     ("trailing_amount", "Nullable(Decimal(38, 10))"),
     ("trailing_type", "LowCardinality(String)"),
     ("single_group", "UInt8"), ("manual_indicator", "UInt8"),
     ("external_operator", "String"), ("referrer", "String"),
     ("broker_strategy", "String"), ("parent_broker_order_id", "String"),
     ("order_group_id", "String"), ("intent_id", "String"),
     ("source_intent_record_id", "UUID"),
     ("source_intent_content_hash", "FixedString(64)"),
     ("oms_group_record_id", "UUID"),
     ("reason", "LowCardinality(String)"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("requested_at", "DateTime64(6, 'UTC')"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)",
    "run_id,account_id,broker_order_id,requested_at,record_id",
)


def _decimal(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) not in (int, float, Decimal):
        raise ValueError("Modify command numeric field has an invalid type")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Modify command numeric field is invalid") from exc
    if (not number.is_finite() or number <= 0
            or number.as_tuple().exponent < -10
            or abs(number) >= Decimal(10) ** 28):
        raise ValueError("Modify command numeric field exceeds its exact scalar contract")
    return str(number)


def project_order_modify_command_v1(
    record: JournalRecord, request: OrderRequest, *,
    attempt_id: str, batch_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Seal a flat request and its causal source identities before dispatch."""
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if (not isinstance(request, OrderRequest)
            or not record.run_id or record.sequence < 1
            or (record.category, record.entity_type)
               != ("command", "order_modify")
            or not record.entity_id or record.account_id != request.acctId
            or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None
            or request.strategyParameters or request.raw
            or not request.cOID or not request.ticker
            or request.ticker != request.ticker.upper()
            or not isinstance(record.payload, dict)):
        raise ValueError("Modify command lacks an exact flat broker request")
    payload = record.payload
    expected = {"strategy_id", "strategy_revision", "correlation_id",
                "causation_id", "order_group_id", "intent_id",
                "source_intent_record_id", "source_intent_content_hash",
                "oms_group_record_id", "reason"}
    if (set(payload) != expected
            or (payload["strategy_id"], payload["strategy_revision"])
               != (STRATEGY_ID, STRATEGY_NUMBER)
            or any(type(payload[key]) is not str or not payload[key]
                   for key in expected - {"strategy_revision"})
            or payload["causation_id"] != payload["intent_id"]
            or not re.fullmatch(r"[a-z][a-z0-9_]*", payload["reason"])
            or not re.fullmatch(r"[0-9a-f]{64}",
                                payload["source_intent_content_hash"])):
        raise ValueError("Modify command lacks normalized Strategy 1 lineage")
    for key in ("source_intent_record_id", "oms_group_record_id"):
        UUID(payload[key])
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1).isoformat()
    event = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "attempt_id": attempt_id,
        "batch_id": batch_id, "sequence": record.sequence,
        "account_id": record.account_id,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "correlation_id": payload["correlation_id"],
        "causation_id": payload["causation_id"],
    }
    detail = typed_row(MODIFY_COMMAND.name, {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "account_id": record.account_id, "broker_order_id": record.entity_id,
        "client_order_id": request.cOID, "conid": request.conid,
        "ticker": request.ticker, "side": request.side,
        "order_type": request.orderType, "time_in_force": request.tif,
        "quantity": _decimal(request.quantity),
        "cash_quantity": _decimal(request.cashQty),
        "limit_price": _decimal(request.price),
        "aux_price": _decimal(request.auxPrice),
        "outside_rth": int(request.outsideRTH),
        "security_type": request.secType,
        "listing_exchange": request.listingExchange,
        "trailing_amount": _decimal(request.trailingAmt),
        "trailing_type": request.trailingType or "",
        "single_group": int(request.isSingleGroup),
        "manual_indicator": int(request.manualIndicator),
        "external_operator": request.extOperator or "",
        "referrer": request.referrer or "",
        "broker_strategy": request.strategy or "",
        "parent_broker_order_id": request.parentId or "",
        "order_group_id": payload["order_group_id"],
        "intent_id": payload["intent_id"],
        "source_intent_record_id": payload["source_intent_record_id"],
        "source_intent_content_hash": payload["source_intent_content_hash"],
        "oms_group_record_id": payload["oms_group_record_id"],
        "reason": payload["reason"],
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
        "requested_at": record.event_time.astimezone(timezone.utc).isoformat(),
    })
    return event, detail
