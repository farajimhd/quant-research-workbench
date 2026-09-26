"""Closed, scalar cancellation activity for the numbered Backtest journal.

The command and broker result retain separate event identities. A broker
result is never inferred from a command, and no opaque response is persisted.
"""
from __future__ import annotations

from datetime import timezone
from datetime import date
from typing import Any
from uuid import UUID

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord


CANCEL = TableContract(
    "trading_order_cancel_activity_v4",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("broker_order_id", "String"), ("order_group_id", "String"),
     ("reason", "String"), ("result_kind", "LowCardinality(String)"),
     ("order_status", "String"), ("message", "String"),
     ("conid", "Nullable(UInt64)"), ("ticker", "LowCardinality(String)"),
     ("intent_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,broker_order_id,record_id",
)


def project_order_cancel_v4(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Tabularize an exact command or simulated broker cancellation reply."""
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    kind = (record.category, record.entity_type)
    if (kind not in {("command", "order_cancel"),
                     ("broker", "order_cancel_requested")}
            or not record.run_id or not record.account_id or not record.entity_id
            or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None
            or not isinstance(record.payload, dict)):
        raise ValueError("Cancellation event envelope is invalid")
    payload = record.payload
    common = {"strategy_id", "strategy_revision", "correlation_id",
              "causation_id"}
    if (not common <= set(payload)
            or any(type(payload[key]) is not str or not payload[key]
                   for key in ("strategy_id", "correlation_id", "causation_id"))
            or type(payload["strategy_revision"]) is not int
            or not 0 < payload["strategy_revision"] <= 0xFFFFFFFF):
        raise ValueError("Cancellation lineage is incomplete")
    group_id = str(payload.get("order_group_id") or "")
    ticker = str(payload.get("ticker") or "")
    intent_id = str(payload.get("intent_id") or "")
    reason = str(payload.get("reason") or "")
    if (ticker and ticker != ticker.upper()
            or any(key in payload and type(payload[key]) is not str
                   for key in ("order_group_id", "ticker", "intent_id", "reason"))):
        raise ValueError("Cancellation identity is invalid")
    result_kind, status, message, conid = "command", "", "", None
    if kind == ("command", "order_cancel"):
        if (set(payload) != common | {"reason", "ticker"}
                or reason != "replace_strategy_protection" or not ticker):
            raise ValueError("Cancellation command has unmodeled fields")
    else:
        reply = payload.get("broker_response", {
            key: value for key, value in payload.items() if key not in common})
        if "broker_response" in payload:
            if (set(payload) != common | {"order_group_id", "reason",
                                       "broker_response", "ticker", "action",
                                       "intent_id"}
                    or not group_id or not reason or not ticker or not intent_id
                    or type(payload["action"]) is not str
                    or not payload["action"] or not isinstance(reply, dict)):
                raise ValueError("Cancellation result has unmodeled group fields")
        elif set(payload) - common not in (
            {"msg", "order_id", "conid", "account"},
            {"msg", "order_id", "status"},
        ):
            raise ValueError("Cancellation result has unmodeled fields")
        if set(reply) == {"msg", "order_id", "conid", "account"}:
            if (reply["msg"] != "Request was submitted"
                    or str(reply["order_id"]) != record.entity_id
                    or type(reply["conid"]) is not int or reply["conid"] <= 0
                    or reply["account"] != record.account_id):
                raise ValueError("Submitted cancellation reply is invalid")
            result_kind, message, conid = "submitted", reply["msg"], reply["conid"]
        elif set(reply) == {"already_terminal"} and group_id:
            if type(reply["already_terminal"]) is not str or not reply["already_terminal"]:
                raise ValueError("Terminal cancellation reply is invalid")
            result_kind, status = "already_terminal", reply["already_terminal"]
        elif set(reply) == {"msg", "order_id", "status"} and not group_id:
            if (reply["msg"] != "Order was already terminal during protection replacement"
                    or str(reply["order_id"]) != record.entity_id
                    or type(reply["status"]) is not str or not reply["status"]):
                raise ValueError("Replacement cancellation reply is invalid")
            result_kind, status, message = "replacement_terminal", reply["status"], reply["msg"]
        else:
            raise ValueError("Cancellation reply is not a closed simulated shape")
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
    detail = typed_row(CANCEL.name, {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": month, "batch_id": batch_id,
        "broker_order_id": record.entity_id, "order_group_id": group_id,
        "reason": reason, "result_kind": result_kind,
        "order_status": status, "message": message, "conid": conid,
        "ticker": ticker, "intent_id": intent_id,
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
    })
    return event, detail


def order_cancel_batch_v4(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
):
    from .arte_journal_writer import TypedJournalBatch, V4OrderCancelBatch

    event, detail = project_order_cancel_v4(
        record, attempt_id=attempt_id, batch_id=batch_id)
    if event["event_month"] != run_month.isoformat():
        raise ValueError("Cancellation event differs from the run month")
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
    )
    return V4OrderCancelBatch(base, detail)
