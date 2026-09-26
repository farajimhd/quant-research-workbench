"""Normalized Strategy 1 risk actions with exact ordered broker replies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord


ACTION = TableContract(
    "trading_risk_action_v4",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("account_id", "String"), ("action_kind", "LowCardinality(String)"),
     ("order_group_id", "String"), ("action_entity_id", "String"),
     ("reason", "String"), ("ticker", "LowCardinality(String)"),
     ("intent_id", "String"),
     ("quantity", "Nullable(Decimal(38, 10))"),
     ("limit_price", "Nullable(Decimal(38, 10))"),
     ("fallback_stop", "Nullable(Decimal(38, 10))"),
     ("reply_count", "UInt8"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
REPLY = TableContract(
    "trading_risk_action_reply_v4",
    (("record_id", "UUID"), ("parent_record_id", "UUID"),
     ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("ordinal", "UInt8"),
     ("reply_kind", "LowCardinality(String)"),
     ("broker_order_id", "String"), ("order_status", "String"),
     ("local_order_id", "String"), ("message", "String"),
     ("conid", "Nullable(UInt64)"), ("account_id", "String"),
     ("error_text", "String"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id",
)
TABLES = (ACTION, REPLY)


def _number(value: object) -> str:
    if type(value) not in (int, float, Decimal):
        raise ValueError("Risk action scalar must be numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Risk action scalar is invalid") from exc
    if (not number.is_finite() or number <= 0
            or number.as_tuple().exponent < -10
            or number >= Decimal(10) ** 28):
        raise ValueError("Risk action scalar exceeds positive decimal range")
    return str(number)


def seal_risk_action_v4(parents: Sequence[Mapping[str, Any]],
                        replies: Sequence[Mapping[str, Any]],
                        events: Sequence[Mapping[str, Any]], *,
                        run_id: str, batch_id: str) -> None:
    by_event = {str(UUID(str(row["record_id"]))): row for row in events}
    by_parent = {str(UUID(str(row["record_id"]))): row for row in parents}
    if (len(by_event) != len(events) or len(by_parent) != len(parents)
            or len({str(UUID(str(row["record_id"]))) for row in replies}) != len(replies)):
        raise ValueError("Risk action has duplicate row identities")
    expected = {identity for identity, event in by_event.items()
                if (event["category"], event["entity_type"]) in
                {("risk", "kill_entry_order"), ("risk", "emergency_flatten")}}
    if set(by_parent) != expected:
        raise ValueError("Risk action lacks exact typed event coverage")
    for identity, parent in by_parent.items():
        event = by_event[identity]
        children = [row for row in replies
                    if str(UUID(str(row["parent_record_id"]))) == identity]
        if (parent["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(parent["batch_id"]))) != batch_id
                or str(UUID(str(event["batch_id"]))) != batch_id
                or parent["event_month"] != event["event_month"]
                or parent["account_id"] != event["account_id"]
                or parent["action_kind"] != event["entity_type"]
                or parent["action_entity_id"] != event["entity_id"]
                or len(children) != parent["reply_count"]
                or {row["ordinal"] for row in children} != set(range(len(children)))):
            raise ValueError("Risk action parent/reply set differs from event")
        for child in children:
            if (child["run_id"] != run_id
                    or str(UUID(str(child["batch_id"]))) != batch_id
                    or child["event_month"] != parent["event_month"]):
                raise ValueError("Risk action reply differs from its parent")
    if any(str(UUID(str(row["parent_record_id"]))) not in by_parent
           for row in replies):
        raise ValueError("Risk action reply has no parent")


@dataclass(frozen=True, slots=True)
class V4RiskActionBatch:
    base: Any
    action: Mapping[str, Any]
    replies: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        def freeze(row: Mapping[str, Any]) -> Mapping[str, Any]:
            if (not isinstance(row, Mapping)
                    or any(isinstance(value, (Mapping, list, tuple, set, bytes))
                           for value in row.values())):
                raise ValueError("Risk action supplement is not scalar")
            return MappingProxyType(dict(row))
        object.__setattr__(self, "action", freeze(self.action))
        object.__setattr__(self, "replies", tuple(freeze(row) for row in self.replies))


def project_risk_action_v4(record: JournalRecord, *, attempt_id: str,
                           batch_id: str) -> tuple[dict, dict, tuple[dict, ...]]:
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    kind = (record.category, record.entity_type)
    if (kind not in {("risk", "kill_entry_order"),
                     ("risk", "emergency_flatten")}
            or not record.run_id or not record.account_id or not record.entity_id
            or record.event_time.tzinfo is None or record.recorded_at.tzinfo is None
            or not isinstance(record.payload, dict)):
        raise ValueError("Risk action event envelope is invalid")
    payload = record.payload
    shared = {"reason", "broker_response", "strategy_id", "strategy_revision",
              "correlation_id", "causation_id"}
    kill = shared | {"order_group_id", "ticker", "action", "intent_id"}
    flatten = shared | {"ticker", "quantity", "limit_price", "fallback_stop"}
    if (set(payload) != (kill if kind[1] == "kill_entry_order" else flatten)
            or any(type(payload[key]) is not str or not payload[key]
                   or len(payload[key]) > 1024
                   for key in ("reason", "ticker", "strategy_id",
                               "correlation_id", "causation_id"))
            or payload["ticker"] != payload["ticker"].upper()
            or type(payload["strategy_revision"]) is not int
            or not 0 < payload["strategy_revision"] <= 0xFFFFFFFF):
        raise ValueError("Risk action fields or lineage are invalid")
    group_id = intent_id = ""
    quantity = limit_price = fallback_stop = None
    if kind[1] == "kill_entry_order":
        if (any(type(payload[key]) is not str or not payload[key]
                for key in ("order_group_id", "action", "intent_id"))
                or type(payload["broker_response"]) is not dict):
            raise ValueError("Risk kill entry identity or reply is invalid")
        group_id, intent_id = payload["order_group_id"], payload["intent_id"]
        sources = [payload["broker_response"]]
    else:
        quantity = _number(payload["quantity"])
        limit_price = _number(payload["limit_price"])
        fallback_stop = _number(payload["fallback_stop"])
        sources = payload["broker_response"]
        if type(sources) is not list or len(sources) != 2:
            raise ValueError("Emergency flatten needs two broker replies")
    month = record.event_time.astimezone(timezone.utc).date().replace(day=1).isoformat()
    common = {"run_id": record.run_id, "event_month": month,
              "batch_id": batch_id}
    event = {
        **common, "record_id": record.record_id, "attempt_id": attempt_id,
        "sequence": record.sequence, "account_id": record.account_id,
        "event_time": record.event_time.astimezone(timezone.utc).isoformat(),
        "recorded_at": record.recorded_at.astimezone(timezone.utc).isoformat(),
        "category": record.category, "entity_type": record.entity_type,
        "entity_id": record.entity_id,
        "correlation_id": payload["correlation_id"],
        "causation_id": payload["causation_id"],
    }
    parent = typed_row(ACTION.name, {
        **common, "record_id": record.record_id,
        "account_id": record.account_id, "action_kind": kind[1],
        "order_group_id": group_id,
        "action_entity_id": record.entity_id,
        "reason": payload["reason"], "ticker": payload["ticker"],
        "intent_id": intent_id, "quantity": quantity,
        "limit_price": limit_price, "fallback_stop": fallback_stop,
        "reply_count": len(sources), "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
    })
    replies = []
    for ordinal, source in enumerate(sources):
        if type(source) is not dict:
            raise ValueError("Risk broker reply is not tabular")
        reply_kind = order_id = status = local_id = message = error = ""
        conid = None
        account = record.account_id
        if set(source) == {"msg", "order_id", "conid", "account"} and group_id:
            if (source["msg"] != "Request was submitted"
                    or str(source["order_id"]) != record.entity_id
                    or type(source["conid"]) is not int or source["conid"] <= 0
                    or source["account"] != record.account_id):
                raise ValueError("Risk cancellation reply is invalid")
            reply_kind, order_id, message, conid = (
                "cancel_submitted", record.entity_id, source["msg"], source["conid"])
        elif set(source) == {"error"} and group_id:
            if (type(source["error"]) is not str or not source["error"]
                    or len(source["error"]) > 1024):
                raise ValueError("Risk cancellation error is invalid")
            reply_kind, error = "cancel_error", source["error"]
        elif set(source) == {"order_id", "order_status", "local_order_id"} and not group_id:
            if (type(source["order_id"]) is not str or not source["order_id"]
                    or source["order_status"] not in {"Submitted", "Inactive"}
                    or type(source["local_order_id"]) is not str
                    or source["local_order_id"] !=
                    f"{record.entity_id}-{'limit' if ordinal == 0 else 'stop'}"):
                raise ValueError("Emergency flatten reply is invalid")
            reply_kind, order_id, status, local_id = (
                "placement", source["order_id"], source["order_status"],
                source["local_order_id"])
        else:
            raise ValueError("Risk action has unmodeled broker reply")
        replies.append(typed_row(REPLY.name, {
            **common,
            "record_id": str(uuid5(NAMESPACE_URL,
                                    f"{record.record_id}:risk-reply:{ordinal}")),
            "parent_record_id": record.record_id, "ordinal": ordinal,
            "reply_kind": reply_kind, "broker_order_id": order_id,
            "order_status": status, "local_order_id": local_id,
            "message": message, "conid": conid, "account_id": account,
            "error_text": error,
        }))
    if len({row["broker_order_id"] for row in replies}) != len(replies) and not group_id:
        raise ValueError("Emergency flatten broker order identities repeat")
    seal_risk_action_v4((parent,), replies, (event,),
                        run_id=record.run_id, batch_id=batch_id)
    return event, parent, tuple(replies)


def risk_action_batch_v4(record: JournalRecord, *, run_month: date,
                         attempt_id: str, batch_id: str,
                         prior_batch_id: str, source_cursor: str):
    from .arte_journal_writer import TypedJournalBatch

    event, parent, replies = project_risk_action_v4(
        record, attempt_id=attempt_id, batch_id=batch_id)
    if run_month.day != 1:
        raise ValueError("Run month must start on day one")
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
    )
    return V4RiskActionBatch(base, parent, replies)
