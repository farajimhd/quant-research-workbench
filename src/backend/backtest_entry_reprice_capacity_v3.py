"""Closed V3 evidence for Portfolio's insufficient entry-reprice capacity."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Mapping, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


CAPACITY = TableContract(
    "trading_entry_reprice_capacity_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("price", "Float64"), ("remaining_quantity", "Float64"),
     ("capacity_quantity", "Float64"),
     ("entry_funding_price", "Nullable(Float64)"),
     ("entry_funding_price_was_int", "Nullable(UInt8)"),
     ("limiting_reason_count", "UInt16"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
REASON = TableContract(
    "trading_entry_reprice_capacity_reason_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("parent_record_id", "UUID"),
     ("account_id", "String"), ("ordinal", "UInt16"),
     ("reason", "LowCardinality(String)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id",
)
TABLES = (CAPACITY, REASON)
_DIMENSIONS = frozenset({
    "order_notional", "available_funds", "gross_exposure", "position",
    "ticker", "strategy", "account_cash_percentage", "net_exposure",
    "planned_risk", "open_risk", "position_count", "sector", "industry",
    "correlation_group", "portfolio_group",
})
_REASONS = frozenset(f"limited_by_{name}" for name in _DIMENSIONS)


@dataclass(frozen=True, slots=True)
class CapacityProjection:
    event: dict[str, Any]
    detail: dict[str, Any]
    reasons: tuple[dict[str, Any], ...]


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Capacity event lacks timezone-aware clock")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _float(value: Any, *, positive: bool = False) -> float:
    if type(value) is not float or not math.isfinite(value) or (positive and value <= 0):
        raise ValueError("Capacity quantity is not a finite source float")
    return value


def _stored_float(value: Any, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Capacity persisted Float64 is invalid")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError("Capacity persisted Float64 is out of range")
    return number


def project_entry_reprice_capacity_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> CapacityProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "entry_reprice_capacity")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Capacity event identity differs")
    payload = record.payload
    fields = {"reason", "limiting_reasons", "price", "remaining_quantity",
              "capacity_quantity", "entry_funding_price", "correlation_id",
              "causation_id"}
    if (not isinstance(payload, Mapping) or set(payload) != fields
            or payload["reason"] != "insufficient_reserved_capacity"
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("correlation_id", "causation_id"))):
        raise ValueError("Capacity payload is not the exact emitted shape")
    price = _float(payload["price"], positive=True)
    remaining = _float(payload["remaining_quantity"], positive=True)
    capacity = _float(payload["capacity_quantity"])
    if capacity < 0 or capacity + 1e-9 >= remaining:
        raise ValueError("Capacity event does not demonstrate insufficiency")
    funding = payload["entry_funding_price"]
    if funding is not None:
        if type(funding) not in (int, float) or not math.isfinite(funding):
            raise ValueError("Capacity funding price is not finite")
        funding_float = float(funding)
        if funding_float <= 0 or not math.isfinite(funding_float) or (
                type(funding) is int and int(funding_float) != funding):
            raise ValueError("Capacity funding price cannot round-trip Float64")
        funding_int = int(type(funding) is int)
    else:
        funding_float = funding_int = None
    reasons = payload["limiting_reasons"]
    if (not isinstance(reasons, list) or not reasons or len(reasons) > len(_REASONS)
            or any(not isinstance(reason, str) for reason in reasons)
            or len(set(reasons)) != len(reasons)
            or any(reason not in _REASONS for reason in reasons)):
        raise ValueError("Capacity reasons are not ordered closed dimensions")
    month = _utc(record.event_time)[:7] + "-01"
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": month, "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    detail = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": month, "batch_id": batch_id,
              "account_id": record.account_id, "price": price,
              "remaining_quantity": remaining, "capacity_quantity": capacity,
              "entry_funding_price": funding_float,
              "entry_funding_price_was_int": funding_int,
              "limiting_reason_count": len(reasons)}
    children = []
    for ordinal, reason in enumerate(reasons):
        child = {"record_id": str(uuid5(NAMESPACE_URL,
                     f"entry-reprice-capacity:{record.record_id}:{ordinal}")),
                 "run_id": record.run_id, "event_month": month,
                 "batch_id": batch_id, "parent_record_id": record.record_id,
                 "account_id": record.account_id, "ordinal": ordinal,
                 "reason": reason}
        children.append({**child, "content_hash": _hash(child)})
    return CapacityProjection(event, {**detail, "content_hash": _hash(detail)},
                              tuple(children))


def recover_entry_reprice_capacity_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
    reasons: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("portfolio_management", "entry_reprice_capacity")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]):
        raise ValueError("Capacity parent/detail identity differs")
    ordered = sorted(reasons, key=lambda row: row["ordinal"])
    if (len(ordered) != detail["limiting_reason_count"]
            or [row["ordinal"] for row in ordered] != list(range(len(ordered)))
            or any(row["parent_record_id"] != detail["record_id"]
                   for row in ordered)):
        raise ValueError("Capacity limiting reasons are incomplete")
    funding = detail["entry_funding_price"]
    if funding is not None:
        funding = _stored_float(funding, positive=True)
        funding = int(funding) if detail["entry_funding_price_was_int"] else funding
    return {"reason": "insufficient_reserved_capacity",
            "limiting_reasons": [row["reason"] for row in ordered],
            "price": _stored_float(detail["price"], positive=True),
            "remaining_quantity": _stored_float(detail["remaining_quantity"], positive=True),
            "capacity_quantity": _stored_float(detail["capacity_quantity"]),
            "entry_funding_price": funding,
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"]}


def seal_entry_reprice_capacity_v3(
    details: Sequence[Mapping[str, Any]], reasons: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]], *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Capacity parent event repeats")
    required = {key for key, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("portfolio_management", "entry_reprice_capacity")}
    found = set()
    child_ids = set()
    parent_hashes = []
    child_hashes = []
    for detail in details:
        if set(detail) != {name for name, _ in CAPACITY.columns}:
            raise ValueError("Capacity detail columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = parents.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or not event["entity_id"]
                or detail["event_month"] != event["event_month"]):
            raise ValueError("Capacity event/detail identity differs")
        found.add(identity)
        owned = [row for row in reasons if row.get("parent_record_id") == identity]
        payload = recover_entry_reprice_capacity_payload(event, detail, owned)
        _stored_float(payload["price"], positive=True)
        _stored_float(payload["remaining_quantity"], positive=True)
        _stored_float(payload["capacity_quantity"])
        if payload["capacity_quantity"] < 0 or (
                payload["capacity_quantity"] + 1e-9 >= payload["remaining_quantity"]):
            raise ValueError("Capacity arithmetic differs")
        if (detail["entry_funding_price"] is None) != (
                detail["entry_funding_price_was_int"] is None):
            raise ValueError("Capacity funding nullable type witness differs")
        if payload["entry_funding_price"] is not None and (
                type(detail["entry_funding_price_was_int"]) is not int or
                detail["entry_funding_price_was_int"] not in (0, 1) or
                float(payload["entry_funding_price"]) != detail["entry_funding_price"]):
            raise ValueError("Capacity funding type witness differs")
        if (not payload["limiting_reasons"] or
                len(set(payload["limiting_reasons"])) != len(payload["limiting_reasons"]) or
                any(reason not in _REASONS for reason in payload["limiting_reasons"])):
            raise ValueError("Capacity limiting dimension differs")
        canonical = {k: v for k, v in detail.items() if k != "content_hash"}
        for field in ("price", "remaining_quantity", "capacity_quantity",
                      "entry_funding_price"):
            if canonical[field] is not None:
                canonical[field] = _stored_float(canonical[field])
        digest = _hash(canonical)
        if digest != detail["content_hash"]:
            raise ValueError("Capacity detail hash differs")
        parent_hashes.append((identity, digest))
        for child in owned:
            if (set(child) != {name for name, _ in REASON.columns}
                    or child["run_id"] != run_id or child["batch_id"] != batch
                    or child["event_month"] != detail["event_month"]
                    or child["account_id"] != detail["account_id"]
                    or child["record_id"] != str(uuid5(NAMESPACE_URL,
                        f"entry-reprice-capacity:{identity}:{child['ordinal']}"))):
                raise ValueError("Capacity reason identity differs")
            child_id = str(UUID(str(child["record_id"])))
            if child_id in child_ids:
                raise ValueError("Capacity reason repeats")
            child_ids.add(child_id)
            child_digest = _hash({k: v for k, v in child.items()
                                  if k != "content_hash"})
            if child_digest != child["content_hash"]:
                raise ValueError("Capacity reason hash differs")
            child_hashes.append((child_id, child_digest))
    if found != required or len(child_ids) != len(reasons):
        raise ValueError("Capacity parent or child is missing or extra")
    return {"entry_reprice_capacity_count": len(details),
            "entry_reprice_capacity_hash": _hash(sorted(parent_hashes)),
            "entry_reprice_capacity_reason_count": len(reasons),
            "entry_reprice_capacity_reason_hash": _hash(sorted(child_hashes))}
