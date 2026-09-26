"""Exact scalar Strategy 1 protection-repair journal contract.

The runtime may keep nested action/reply objects in memory. Publication splits
them into ordered typed rows; no JSON, blob, or response text fallback exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_journal_schema import TableContract
from .journal_contract import JournalRecord


RECONCILIATION = TableContract(
    "trading_protection_reconciliation_v4",
    (("record_id", "UUID"), ("run_id", "String"),
     ("event_month", "Date"), ("batch_id", "UUID"),
     ("account_id", "String"), ("order_group_id", "String"),
     ("intent_id", "String"), ("ticker", "LowCardinality(String)"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("intent_action", "LowCardinality(String)"),
     ("status", "LowCardinality(String)"),
     ("required_quantity", "Decimal(38, 10)"),
     ("protected_quantity", "Decimal(38, 10)"),
     ("action_count", "UInt16"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,order_group_id,record_id",
)
ACTION = TableContract(
    "trading_protection_reconciliation_action_v4",
    (("record_id", "UUID"), ("parent_record_id", "UUID"),
     ("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
     ("ordinal", "UInt16"), ("action", "LowCardinality(String)"),
     ("broker_order_id", "String"),
     ("quantity", "Nullable(Decimal(38, 10))"),
     ("stop_price", "Nullable(Decimal(38, 10))"),
     ("target_price", "Nullable(Decimal(38, 10))"),
     ("from_stop", "Nullable(Decimal(38, 10))"),
     ("to_stop", "Nullable(Decimal(38, 10))"),
     ("reply_count", "UInt16"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id",
)
REPLY = TableContract(
    "trading_protection_reconciliation_reply_v4",
    (("record_id", "UUID"), ("parent_record_id", "UUID"),
     ("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
     ("ordinal", "UInt16"), ("reply_kind", "LowCardinality(String)"),
     ("broker_order_id", "String"), ("order_status", "String"),
     ("local_order_id", "String"), ("message", "String"),
     ("conid", "Nullable(UInt64)"), ("account_id", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id",
)
TABLES = (RECONCILIATION, ACTION, REPLY)


def seal_protection_reconciliation_v4(parents, actions, replies, events,
                                      *, run_id: str, batch_id: str) -> None:
    """Check complete parent/action/reply graph before write and after readback."""
    by_event = {str(UUID(str(row["record_id"]))): row for row in events}
    by_parent = {str(UUID(str(row["record_id"]))): row for row in parents}
    by_action = {str(UUID(str(row["record_id"]))): row for row in actions}
    if (len(by_event) != len(events) or len(by_parent) != len(parents)
            or len(by_action) != len(actions)
            or len({str(UUID(str(row["record_id"]))) for row in replies}) != len(replies)):
        raise ValueError("Protection reconciliation has duplicate identities")
    expected = {identity for identity, event in by_event.items()
                if (event["category"], event["entity_type"]) ==
                ("order_management", "protection_reconciliation")}
    if set(by_parent) != expected:
        raise ValueError("Protection reconciliation lacks exact typed event coverage")
    for identity, parent in by_parent.items():
        event = by_event[identity]
        if (parent["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(parent["batch_id"]))) != batch_id
                or str(UUID(str(event["batch_id"]))) != batch_id
                or parent["event_month"] != event["event_month"]
                or parent["account_id"] != event["account_id"]
                or parent["order_group_id"] != event["entity_id"]):
            raise ValueError("Protection reconciliation differs from its event")
        children = [row for row in actions
                    if str(UUID(str(row["parent_record_id"]))) == identity]
        if (len(children) != int(parent["action_count"])
                or {int(row["ordinal"]) for row in children} != set(range(len(children)))):
            raise ValueError("Protection reconciliation action set is incomplete")
    for identity, action in by_action.items():
        parent_id = str(UUID(str(action["parent_record_id"])))
        if (parent_id not in by_parent or action["run_id"] != run_id
                or str(UUID(str(action["batch_id"]))) != batch_id
                or action["event_month"] != by_parent[parent_id]["event_month"]):
            raise ValueError("Protection reconciliation action has no parent")
        children = [row for row in replies
                    if str(UUID(str(row["parent_record_id"]))) == identity]
        if (len(children) != int(action["reply_count"])
                or {int(row["ordinal"]) for row in children} != set(range(len(children)))):
            raise ValueError("Protection reconciliation reply set is incomplete")
    for reply in replies:
        parent_id = str(UUID(str(reply["parent_record_id"])))
        if (parent_id not in by_action or reply["run_id"] != run_id
                or str(UUID(str(reply["batch_id"]))) != batch_id
                or reply["event_month"] != by_action[parent_id]["event_month"]):
            raise ValueError("Protection reconciliation reply has no action")


@dataclass(frozen=True, slots=True)
class V4ProtectionReconciliationBatch:
    base: Any
    reconciliation: Mapping[str, Any]
    actions: tuple[Mapping[str, Any], ...]
    replies: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        def freeze(row: Mapping[str, Any]) -> Mapping[str, Any]:
            if (not isinstance(row, Mapping)
                    or any(isinstance(value, (Mapping, list, tuple, set,
                                               bytearray, memoryview))
                           for value in row.values())):
                raise ValueError("V4 reconciliation supplement is not a scalar typed row")
            return MappingProxyType(dict(row))

        object.__setattr__(self, "reconciliation", freeze(self.reconciliation))
        object.__setattr__(self, "actions", tuple(freeze(row) for row in self.actions))
        object.__setattr__(self, "replies", tuple(freeze(row) for row in self.replies))


def _number(value: object, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if type(value) not in (int, float, Decimal):
        raise ValueError("Protection reconciliation has a nonnumeric scalar")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Protection reconciliation has an invalid scalar") from exc
    if not number.is_finite():
        raise ValueError("Protection reconciliation has a nonfinite scalar")
    return str(number)


def project_protection_reconciliation_v4(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> tuple[dict, dict, tuple[dict, ...], tuple[dict, ...]]:
    from .arte_journal_writer import typed_row

    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    payload = record.payload
    required = {"order_group_id", "required_quantity", "protected_quantity",
                "actions", "status", "ticker", "action", "intent_id",
                "correlation_id", "causation_id", "strategy_id",
                "strategy_revision"}
    if ((record.category, record.entity_type) !=
            ("order_management", "protection_reconciliation")
            or not record.run_id or not record.account_id
            or not isinstance(payload, dict) or set(payload) != required
            or record.entity_id != payload["order_group_id"]
            or record.event_time.tzinfo is None
            or record.recorded_at.tzinfo is None):
        raise ValueError("Protection reconciliation source envelope is invalid")
    for key in ("order_group_id", "ticker", "action", "intent_id",
                "correlation_id", "causation_id", "strategy_id", "status"):
        if type(payload[key]) is not str or not payload[key]:
            raise ValueError("Protection reconciliation identity is incomplete")
    if (payload["ticker"] != payload["ticker"].upper()
            or type(payload["strategy_revision"]) is not int
            or not 0 < payload["strategy_revision"] <= 0xFFFFFFFF
            or payload["status"] not in {"repaired", "reconciled"}
            or type(payload["actions"]) is not list
            or len(payload["actions"]) > 65535
            or (payload["status"] == "repaired") != bool(payload["actions"])):
        raise ValueError("Protection reconciliation status or actions are invalid")
    month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
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
    detail = typed_row(RECONCILIATION.name, {
        **common, "record_id": record.record_id,
        "account_id": record.account_id,
        "order_group_id": payload["order_group_id"],
        "intent_id": payload["intent_id"], "ticker": payload["ticker"],
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
        "intent_action": payload["action"], "status": payload["status"],
        "required_quantity": _number(payload["required_quantity"]),
        "protected_quantity": _number(payload["protected_quantity"]),
        "action_count": len(payload["actions"]),
    })
    actions = []
    replies = []
    allowed_action = {"action", "order_id", "quantity", "stop_price",
                      "target_price", "from_stop", "to_stop", "response"}
    for ordinal, source in enumerate(payload["actions"]):
        if (not isinstance(source, dict) or set(source) - allowed_action
                or type(source.get("action")) is not str or not source["action"]
                or "response" not in source
                or type(source["response"]) not in (list, dict)):
            raise ValueError("Protection reconciliation action has unmodeled fields")
        response = (source["response"] if isinstance(source["response"], list)
                    else [source["response"]])
        if not response or len(response) > 65535:
            raise ValueError("Protection reconciliation action lacks bounded replies")
        action_id = str(uuid5(NAMESPACE_URL,
                              f"{record.record_id}:reconciliation-action:{ordinal}"))
        actions.append(typed_row(ACTION.name, {
            **common, "record_id": action_id,
            "parent_record_id": record.record_id, "ordinal": ordinal,
            "action": source["action"],
            "broker_order_id": str(source.get("order_id") or ""),
            **{key: _number(source.get(key), optional=True)
               for key in ("quantity", "stop_price", "target_price",
                           "from_stop", "to_stop")},
            "reply_count": len(response),
        }))
        for reply_ordinal, answer in enumerate(response):
            if not isinstance(answer, dict):
                raise ValueError("Protection reconciliation reply is not tabular")
            placement = {"order_id", "order_status", "local_order_id"}
            cancellation = {"msg", "order_id", "conid", "account"}
            if set(answer) == placement:
                if any(type(answer[key]) is not str or not answer[key]
                       for key in placement):
                    raise ValueError("Protection placement reply is invalid")
                kind = "order"
                fields = (str(answer["order_id"]), answer["order_status"],
                          answer["local_order_id"], "", None, "")
            elif set(answer) == cancellation:
                if (type(answer["order_id"]) is not int
                        or type(answer["conid"]) is not int
                        or type(answer["account"]) is not str
                        or answer["account"] != record.account_id
                        or type(answer["msg"]) is not str or not answer["msg"]):
                    raise ValueError("Protection cancellation reply is invalid")
                kind = "cancel"
                fields = (str(answer["order_id"]), "", "", answer["msg"],
                          answer["conid"], answer["account"])
            else:
                raise ValueError("Protection reconciliation reply has unmodeled fields")
            replies.append(typed_row(REPLY.name, {
                **common,
                "record_id": str(uuid5(NAMESPACE_URL,
                    f"{action_id}:reconciliation-reply:{reply_ordinal}")),
                "parent_record_id": action_id, "ordinal": reply_ordinal,
                "reply_kind": kind, "broker_order_id": fields[0],
                "order_status": fields[1], "local_order_id": fields[2],
                "message": fields[3], "conid": fields[4],
                "account_id": fields[5],
            }))
    if len(replies) > 65_535:
        raise ValueError("Protection reconciliation exceeds bounded reply readback")
    return event, detail, tuple(actions), tuple(replies)


def protection_reconciliation_batch_v4(
    record: JournalRecord, *, run_month: date, attempt_id: str,
    batch_id: str, prior_batch_id: str, source_cursor: str,
) -> V4ProtectionReconciliationBatch:
    from .arte_journal_writer import TypedJournalBatch

    event, detail, actions, replies = project_protection_reconciliation_v4(
        record, attempt_id=attempt_id, batch_id=batch_id)
    if run_month.isoformat() != event["event_month"]:
        raise ValueError("Protection reconciliation differs from run month")
    base = TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,))
    return V4ProtectionReconciliationBatch(base, detail, actions, replies)
