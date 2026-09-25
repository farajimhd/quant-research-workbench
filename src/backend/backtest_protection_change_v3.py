"""Typed, lossless V3 protection-change fact; no database writes here."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.journal_decimal import decimal_38_18, source_float_from_decimal


CHANGE = TableContract(
    "trading_protection_change_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("order_group_id", "String"), ("order_id", "String"),
     ("client_order_id", "String"), ("kind", "LowCardinality(String)"),
     ("phase", "LowCardinality(String)"), ("price", "Decimal(38, 18)"),
     ("active", "Bool"), ("ticker", "LowCardinality(String)"),
     ("source_intent_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("action", "Nullable(String)"),
     ("intent_id", "Nullable(String)"), ("entry_order_count", "UInt32"),
     ("entry_order_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
ENTRY_ORDER = TableContract(
    "trading_protection_entry_order_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("ordinal", "UInt16"), ("entry_order_id", "String"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,record_id,ordinal",
)
TABLES = (CHANGE, ENTRY_ORDER)


@dataclass(frozen=True, slots=True)
class ProtectionChangeProjection:
    event: dict[str, Any]
    detail: dict[str, Any]
    entry_orders: tuple[dict[str, Any], ...]


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Protection timestamp lacks timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _payload(payload: Mapping[str, Any], *, entity_id: str) -> tuple[str, tuple[str, ...]]:
    required = {
            "schema_version", "order_group_id", "entry_order_ids", "order_id",
            "client_order_id", "kind", "phase", "price", "active", "ticker",
            "source_intent_id", "strategy_id", "strategy_revision",
            "correlation_id", "causation_id"}
    optional = {"action", "intent_id"}
    if (not isinstance(payload, Mapping) or not required <= set(payload)
            or set(payload) - required not in (set(), optional)):
        raise ValueError("Protection change has unmodeled evidence")
    identifiers = ("order_group_id", "order_id", "client_order_id", "ticker",
                   "source_intent_id", "strategy_id", "correlation_id", "causation_id")
    if (type(payload["schema_version"]) is not int or payload["schema_version"] != 1
            or type(payload["strategy_revision"]) is not int
            or not 0 <= payload["strategy_revision"] <= 0xFFFFFFFF
            or any(type(payload[key]) is not str or not payload[key]
                   for key in identifiers)
            or ("action" in payload and (type(payload["action"]) is not str
                                        or not payload["action"]))
            or ("intent_id" in payload and (type(payload["intent_id"]) is not str
                                           or not payload["intent_id"]))
            or payload["order_id"] != entity_id
            or payload["ticker"] != payload["ticker"].upper()
            or payload["kind"] not in {"stop", "target"}
            or payload["phase"] not in {"requested", "effective"}
            or type(payload["active"]) is not bool):
        raise ValueError("Protection change identity or state differs")
    identifiers_list = payload["entry_order_ids"]
    if (type(identifiers_list) is not list or len(identifiers_list) > 65_535
            or any(type(item) is not str or not item for item in identifiers_list)
            or identifiers_list != sorted(set(identifiers_list))):
        raise ValueError("Protection entry order IDs must be sorted unique")
    price = decimal_38_18(payload["price"], source_float=True, positive=True)
    return price, tuple(identifiers_list)


def project_protection_change_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> ProtectionChangeProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) != ("protection", "protection_change")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Protection event identity differs")
    price, entry_ids = _payload(record.payload, entity_id=record.entity_id)
    month = _utc(record.event_time)[:7] + "-01"
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": month, "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": record.payload["correlation_id"],
             "causation_id": record.payload["causation_id"]}
    child_rows = []
    for ordinal, order_id in enumerate(entry_ids):
        row = {"record_id": record.record_id, "run_id": record.run_id,
               "event_month": month, "batch_id": batch_id,
               "ordinal": ordinal, "entry_order_id": order_id}
        child_rows.append({**row, "content_hash": _hash(row)})
    children = tuple(child_rows)
    detail = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": month, "batch_id": batch_id,
              "account_id": record.account_id,
              "order_group_id": record.payload["order_group_id"],
              "order_id": record.payload["order_id"],
              "client_order_id": record.payload["client_order_id"],
              "kind": record.payload["kind"], "phase": record.payload["phase"],
              "price": price, "active": record.payload["active"],
              "ticker": record.payload["ticker"],
              "source_intent_id": record.payload["source_intent_id"],
              "strategy_id": record.payload["strategy_id"],
              "strategy_revision": record.payload["strategy_revision"],
              "action": record.payload.get("action"),
              "intent_id": record.payload.get("intent_id"),
              "entry_order_count": len(children),
              "entry_order_hash": _hash([(row["ordinal"], row["content_hash"])
                                          for row in children])}
    return ProtectionChangeProjection(event, {**detail, "content_hash": _hash(detail)},
                                      children)


def recover_protection_change_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
    children: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) != ("protection", "protection_change")
            or event["record_id"] != detail["record_id"]
            or event["run_id"] != detail["run_id"]
            or event["account_id"] != detail["account_id"]
            or event["entity_id"] != detail["order_id"]):
        raise ValueError("Protection parent/detail identity differs")
    if type(detail["entry_order_count"]) is not int or detail["entry_order_count"] != len(children):
        raise ValueError("Protection entry-order count differs")
    ids = []
    hashes = []
    for ordinal, child in enumerate(children):
        if (set(child) != {name for name, _ in ENTRY_ORDER.columns}
                or child["record_id"] != event["record_id"]
                or child["run_id"] != event["run_id"]
                or child["event_month"] != detail["event_month"]
                or child["batch_id"] != event["batch_id"]
                or child["ordinal"] != ordinal):
            raise ValueError("Protection entry-order identity differs")
        canonical = {key: child[key] for key, _ in ENTRY_ORDER.columns
                     if key != "content_hash"}
        if child["content_hash"] != _hash(canonical):
            raise ValueError("Protection entry-order content differs")
        ids.append(child["entry_order_id"])
        hashes.append((ordinal, child["content_hash"]))
    if detail["entry_order_hash"] != _hash(hashes):
        raise ValueError("Protection entry-order seal differs")
    payload = {"schema_version": 1, "order_group_id": detail["order_group_id"],
               "entry_order_ids": ids, "order_id": detail["order_id"],
               "client_order_id": detail["client_order_id"],
               "kind": detail["kind"], "phase": detail["phase"],
               "price": source_float_from_decimal(detail["price"], positive=True),
               "active": detail["active"], "ticker": detail["ticker"],
               "source_intent_id": detail["source_intent_id"],
               "strategy_id": detail["strategy_id"],
               "strategy_revision": detail["strategy_revision"],
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    if detail["action"] is not None or detail["intent_id"] is not None:
        if detail["action"] is None or detail["intent_id"] is None:
            raise ValueError("Protection action/intent identity is partial")
        payload["action"] = detail["action"]
        payload["intent_id"] = detail["intent_id"]
    _payload(payload, entity_id=event["entity_id"])
    return payload


def seal_protection_changes_v3(
    details: Sequence[Mapping[str, Any]], children: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]], *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Protection parent event repeats")
    required = {key for key, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("protection", "protection_change")}
    by_parent: dict[str, list[Mapping[str, Any]]] = {}
    for child in children:
        by_parent.setdefault(str(UUID(str(child["record_id"]))), []).append(child)
    if set(by_parent) - required:
        raise ValueError("Orphan protection entry order")
    found = set()
    hashes = []
    for detail in details:
        if set(detail) != {name for name, _ in CHANGE.columns}:
            raise ValueError("Protection detail columns differ")
        identity = str(UUID(str(detail["record_id"])))
        parent = parents.get(identity)
        if (identity in found or identity not in required or parent is None
                or detail["run_id"] != run_id or parent["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(parent["batch_id"]))) != batch
                or detail["account_id"] != parent["account_id"]
                or detail["event_month"] != parent["event_month"]):
            raise ValueError("Protection detail parent identity differs")
        found.add(identity)
        recover_protection_change_payload(parent, detail, by_parent.get(identity, ()))
        canonical = {key: detail[key] for key, _ in CHANGE.columns
                     if key != "content_hash"}
        canonical["price"] = decimal_38_18(canonical["price"], positive=True)
        digest = _hash(canonical)
        if detail["content_hash"] != digest:
            raise ValueError("Protection detail content differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Protection event lacks typed detail")
    return {"protection_change_count": len(details),
            "protection_change_hash": _hash(sorted(hashes))}
