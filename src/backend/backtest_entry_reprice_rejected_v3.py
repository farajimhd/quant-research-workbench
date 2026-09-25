"""Closed V3 scalar evidence for Portfolio's invalid-protection reprice refusal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


REJECTED = TableContract(
    "trading_entry_reprice_rejected_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("reason_detail", "String"), ("price", "Float64"),
     ("remaining_quantity", "Float64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)


@dataclass(frozen=True, slots=True)
class RejectedProjection:
    event: dict[str, Any]
    detail: dict[str, Any]


def _hash(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Entry reprice refusal lacks timezone-aware event time")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _float(value: Any, *, source: bool) -> float:
    if (source and type(value) is not float) or (
            not source and type(value) not in (int, float)):
        raise ValueError("Entry reprice refusal numeric type differs")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("Entry reprice refusal quantity is not finite positive")
    return number


def project_entry_reprice_rejected_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> RejectedProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "entry_reprice_rejected")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Entry reprice refusal event identity differs")
    payload = record.payload
    fields = {"reason", "detail", "price", "remaining_quantity",
              "correlation_id", "causation_id"}
    if (not isinstance(payload, Mapping) or set(payload) != fields
            or payload["reason"] != "invalid_protection_at_reprice"
            or not isinstance(payload["detail"], str)
            or not payload["detail"]
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("correlation_id", "causation_id"))):
        raise ValueError("Entry reprice refusal has unmodeled source evidence")
    price = _float(payload["price"], source=True)
    remaining = _float(payload["remaining_quantity"], source=True)
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
              "account_id": record.account_id,
              "reason_detail": payload["detail"], "price": price,
              "remaining_quantity": remaining}
    return RejectedProjection(event, {**detail, "content_hash": _hash(detail)})


def recover_entry_reprice_rejected_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("portfolio_management", "entry_reprice_rejected")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]):
        raise ValueError("Entry reprice refusal event/detail identity differs")
    return {"reason": "invalid_protection_at_reprice",
            "detail": detail["reason_detail"],
            "price": _float(detail["price"], source=False),
            "remaining_quantity": _float(detail["remaining_quantity"], source=False),
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"]}


def seal_entry_reprice_rejected_v3(
    details: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Entry reprice refusal parent event repeats")
    required = {key for key, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("portfolio_management", "entry_reprice_rejected")}
    found = set()
    hashes = []
    for detail in details:
        if set(detail) != {name for name, _ in REJECTED.columns}:
            raise ValueError("Entry reprice refusal detail columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = parents.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or detail["event_month"] != event["event_month"]
                or not event["entity_id"]):
            raise ValueError("Entry reprice refusal parent identity differs")
        found.add(identity)
        payload = recover_entry_reprice_rejected_payload(event, detail)
        if not isinstance(payload["detail"], str) or not payload["detail"]:
            raise ValueError("Entry reprice refusal diagnostic is empty")
        canonical = {key: value for key, value in detail.items()
                     if key != "content_hash"}
        for field in ("price", "remaining_quantity"):
            canonical[field] = _float(canonical[field], source=False)
        digest = _hash(canonical)
        if detail["content_hash"] != digest:
            raise ValueError("Entry reprice refusal detail hash differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Entry reprice refusal parent lacks exact typed child")
    return {"entry_reprice_rejected_count": len(details),
            "entry_reprice_rejected_hash": _hash(sorted(hashes))}
