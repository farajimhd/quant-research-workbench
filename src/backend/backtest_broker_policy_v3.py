"""Inactive closed typed facts for simulated OMS reply-policy emissions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


POLICY_EVENT = TableContract(
    "trading_broker_reply_policy_event_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("policy_version", "UInt32"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("order_group_id", "Nullable(String)"),
     ("ticker", "Nullable(String)"), ("action", "Nullable(String)"),
     ("intent_id", "Nullable(String)"),
     ("confirmed", "Nullable(UInt8)"),
     ("request_message_count", "UInt16"),
     ("response_message_count", "UInt16"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)

MESSAGE = TableContract(
    "trading_broker_reply_policy_message_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("parent_record_id", "UUID"),
     ("account_id", "String"), ("role", "LowCardinality(String)"),
     ("ordinal", "UInt16"), ("message_id", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,role,ordinal,record_id",
)


@dataclass(frozen=True, slots=True)
class BrokerPolicyProjection:
    event: Mapping[str, Any]
    detail: Mapping[str, Any]
    messages: tuple[Mapping[str, Any], ...]


def _utc(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Broker policy event time lacks UTC authority")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _digest(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode("utf-8")).hexdigest()


def _ids(value: Any, *, bound: int = 51) -> list[str]:
    if (not isinstance(value, list) or len(value) > bound
            or any(not isinstance(item, str) or not item for item in value)):
        raise ValueError("Broker policy message IDs are not a bounded scalar list")
    return value


def project_broker_reply_policy_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> BrokerPolicyProjection:
    """Exact source projection; open broker responses never enter typed storage."""
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if (record.category != "broker_policy" or record.entity_type not in {
            "order_reply_suppression", "order_warning_decision"}
            or not record.run_id or not record.entity_id):
        raise ValueError("Broker reply policy identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Broker reply policy payload is not a mapping")
    common = {"policy_version", "strategy_id", "strategy_revision",
              "correlation_id", "causation_id"}
    if (any(type(payload.get(key)) is not int or not 0 <= payload[key] < 2**32
            for key in ("policy_version", "strategy_revision"))
            or any(not isinstance(payload.get(key), str) or not payload[key]
                   for key in ("strategy_id", "correlation_id", "causation_id"))):
        raise ValueError("Broker reply policy common evidence differs")
    parent = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": _utc(record.event_time)[:7] + "-01",
              "batch_id": batch_id, "account_id": record.account_id,
              "policy_version": payload["policy_version"],
              "strategy_id": payload["strategy_id"],
              "strategy_revision": payload["strategy_revision"],
              "order_group_id": None,
              "ticker": None, "action": None, "intent_id": None,
              "confirmed": None,
              "request_message_count": 0, "response_message_count": 0}
    if record.entity_type == "order_reply_suppression":
        if (set(payload) != common | {"message_ids", "broker_response"}
                or record.account_id
                or record.entity_id != f"policy-v{payload['policy_version']}"):
            raise ValueError("Reply suppression payload differs")
        requested = _ids(payload["message_ids"])
        if not requested:
            raise ValueError("Reply suppression has no requested message IDs")
        response = payload["broker_response"]
        if (not isinstance(response, Mapping)
                or set(response) != {"status", "messageIds"}
                or response["status"] != "submitted"):
            raise ValueError("Reply suppression response is open or differs")
        responded = _ids(response["messageIds"])
        if responded != list(dict.fromkeys(requested)):
            raise ValueError("Reply suppression response differs from requested IDs")
        parent.update(request_message_count=len(requested),
                      response_message_count=len(responded))
        groups = (("request", requested), ("response", responded))
    else:
        fields = common | {"order_group_id", "message_ids", "confirmed",
                           "known", "ticker", "action", "intent_id"}
        if (set(payload) != fields or not record.account_id
                or any(not isinstance(payload[key], str) or not payload[key]
                       for key in ("order_group_id", "ticker", "action", "intent_id"))
                or any(type(payload[key]) is not bool
                       for key in ("confirmed", "known"))):
            raise ValueError("Warning decision payload differs")
        requested = _ids(payload["message_ids"])
        if payload["confirmed"] != payload["known"]:
            raise ValueError("Warning decision disagrees with known policy IDs")
        parent.update(order_group_id=payload["order_group_id"],
                      ticker=payload["ticker"], action=payload["action"],
                      intent_id=payload["intent_id"],
                      confirmed=int(payload["confirmed"]),
                      request_message_count=len(requested))
        groups = (("warning", requested),)
    common_child = {key: parent[key] for key in (
        "run_id", "event_month", "batch_id", "account_id")}
    messages = []
    for role, ids in groups:
        for ordinal, message_id in enumerate(ids):
            row = {**common_child,
                   "record_id": str(uuid5(NAMESPACE_URL,
                       f"{record.record_id}:broker-policy:{role}:{ordinal}")),
                   "parent_record_id": record.record_id, "role": role,
                   "ordinal": ordinal, "message_id": message_id}
            messages.append({**row, "content_hash": _digest(row)})
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": parent["event_month"], "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    return BrokerPolicyProjection(event, {**parent, "content_hash": _digest(parent)},
                                  tuple(messages))


def recover_broker_reply_policy_payload(
    event: Mapping[str, Any], parent: Mapping[str, Any],
    messages: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconstruct only the two exact OMS payload variants."""
    if (event["record_id"] != parent["record_id"]
            or event["category"] != "broker_policy"
            or event["entity_type"] not in {
                "order_reply_suppression", "order_warning_decision"}):
        raise ValueError("Reply policy event/parent differs")
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in messages:
        groups.setdefault(row["role"], []).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row: row["ordinal"])
        if [row["ordinal"] for row in rows] != list(range(len(rows))):
            raise ValueError("Reply policy message ordinal differs")
    ids = {role: [row["message_id"] for row in rows]
           for role, rows in groups.items()}
    payload = {"policy_version": parent["policy_version"],
               "strategy_id": parent["strategy_id"],
               "strategy_revision": parent["strategy_revision"],
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    if event["entity_type"] == "order_reply_suppression":
        requested = ids.get("request", [])
        responded = ids.get("response", [])
        if (set(ids) - {"request", "response"}
                or parent["request_message_count"] != len(requested)
                or parent["response_message_count"] != len(responded)
                or not requested or responded != list(dict.fromkeys(requested))
                or any(parent[key] is not None for key in (
                    "order_group_id", "ticker", "action", "intent_id",
                    "confirmed",))):
            raise ValueError("Reply suppression child set differs")
        payload.update(message_ids=requested,
                       broker_response={"status": "submitted",
                                        "messageIds": responded})
    elif event["entity_type"] == "order_warning_decision":
        warning = ids.get("warning", [])
        if (set(ids) - {"warning"}
                or parent["request_message_count"] != len(warning)
                or parent["response_message_count"] != 0
                or any(not isinstance(parent[key], str) or not parent[key]
                       for key in ("order_group_id", "ticker", "action", "intent_id"))
                or type(parent["confirmed"]) is not int
                or parent["confirmed"] not in (0, 1)):
            raise ValueError("Warning decision child set differs")
        payload.update(order_group_id=parent["order_group_id"],
                       message_ids=warning, confirmed=bool(parent["confirmed"]),
                       known=bool(parent["confirmed"]), ticker=parent["ticker"],
                       action=parent["action"], intent_id=parent["intent_id"])
    else:
        raise ValueError("Reply policy variant is not closed")
    return payload


def seal_broker_reply_policy_v3(
    parents: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    messages: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    events: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    """Seal ordered scalar IDs with exact one-parent-per-event causality."""
    batch = str(UUID(batch_id))
    events_by_id = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(events_by_id) != len(events):
        raise ValueError("Reply policy parent event repeats")
    required = {key for key, row in events_by_id.items() if
                row["category"] == "broker_policy" and row["entity_type"] in {
                    "order_reply_suppression", "order_warning_decision"}}
    parent_fields = {name for name, _ in POLICY_EVENT.columns}
    child_fields = {name for name, _ in MESSAGE.columns}
    parent_by_id = {}
    parent_hashes = []
    for parent in parents:
        if set(parent) != parent_fields:
            raise ValueError("Reply policy parent columns differ")
        identity = str(UUID(str(parent["record_id"])))
        event = events_by_id.get(identity)
        if (identity in parent_by_id or identity not in required or event is None
                or parent["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(parent["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or parent["event_month"] != event["event_month"]
                or parent["account_id"] != event["account_id"]):
            raise ValueError("Reply policy parent identity differs")
        if (event["entity_type"] == "order_reply_suppression"
                and (event["entity_id"] != f"policy-v{parent['policy_version']}"
                     or event["account_id"])):
            raise ValueError("Reply suppression event identity differs")
        if (event["entity_type"] == "order_warning_decision"
                and (not event["account_id"] or not event["entity_id"])):
            raise ValueError("Warning decision event identity differs")
        digest = _digest({key: value for key, value in parent.items()
                          if key != "content_hash"})
        if digest != parent["content_hash"]:
            raise ValueError("Reply policy parent hash differs")
        parent_by_id[identity] = parent
        parent_hashes.append((identity, digest))
    if set(parent_by_id) != required:
        raise ValueError("Reply policy event lacks exact parent")
    child_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    child_hashes = []
    seen = set()
    for row in messages:
        if set(row) != child_fields:
            raise ValueError("Reply policy message columns differ")
        identity = str(UUID(str(row["record_id"])))
        parent_id = str(UUID(str(row["parent_record_id"])))
        parent = parent_by_id.get(parent_id)
        role, ordinal = row["role"], row["ordinal"]
        if (identity in seen or parent is None
                or role not in {"request", "response", "warning"}
                or type(ordinal) is not int or not 0 <= ordinal < 2**16
                or identity != str(uuid5(NAMESPACE_URL,
                    f"{parent_id}:broker-policy:{role}:{ordinal}"))
                or row["run_id"] != run_id or str(UUID(str(row["batch_id"]))) != batch
                or any(row[key] != parent[key] for key in
                       ("event_month", "account_id"))
                or not isinstance(row["message_id"], str) or not row["message_id"]):
            raise ValueError("Reply policy message identity differs")
        digest = _digest({key: value for key, value in row.items()
                          if key != "content_hash"})
        if digest != row["content_hash"]:
            raise ValueError("Reply policy message hash differs")
        seen.add(identity)
        child_by_parent.setdefault(parent_id, []).append(row)
        child_hashes.append((identity, digest))
    for identity, parent in parent_by_id.items():
        recover_broker_reply_policy_payload(
            events_by_id[identity], parent, child_by_parent.get(identity, []))
    return {
        "broker_reply_policy_event_count": len(parents),
        "broker_reply_policy_event_hash": _digest(sorted(parent_hashes)),
        "broker_reply_policy_message_count": len(messages),
        "broker_reply_policy_message_hash": _digest(sorted(child_hashes)),
    }
