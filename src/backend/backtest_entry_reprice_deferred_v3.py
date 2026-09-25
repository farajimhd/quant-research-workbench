"""Inactive closed typed fact for capacity-deferred adaptive entry repricing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


DEFERRED = TableContract(
    "trading_entry_reprice_deferred_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("requested_price", "Float64"), ("remaining_quantity", "Float64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)


@dataclass(frozen=True, slots=True)
class DeferredProjection:
    event: Mapping[str, Any]
    detail: Mapping[str, Any]


def _utc(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Deferred reprice event time lacks UTC authority")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _digest(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode("utf-8")).hexdigest()


def project_entry_reprice_deferred_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> DeferredProjection:
    """Accept only the one OMS capacity-deferral source payload."""
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("order_management", "entry_reprice_deferred")
            or not record.run_id or not record.entity_id or not record.account_id):
        raise ValueError("Deferred reprice journal identity differs")
    payload = record.payload
    fields = {"reason", "requested_price", "remaining_quantity",
              "strategy_id", "strategy_revision", "correlation_id",
              "causation_id"}
    if (not isinstance(payload, Mapping) or set(payload) != fields
            or payload["reason"] != "portfolio_allocation_capacity"
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("strategy_id", "correlation_id", "causation_id"))
            or type(payload["strategy_revision"]) is not int
            or not 0 <= payload["strategy_revision"] < 2**32
            or any(type(payload[key]) is not float or not math.isfinite(payload[key])
                   for key in ("requested_price", "remaining_quantity"))
            or payload["requested_price"] <= 0
            or payload["remaining_quantity"] < 0):
        raise ValueError("Deferred reprice payload is not exact finite scalar evidence")
    detail = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": _utc(record.event_time)[:7] + "-01",
              "batch_id": batch_id, "account_id": record.account_id,
              "strategy_id": payload["strategy_id"],
              "strategy_revision": payload["strategy_revision"],
              "requested_price": payload["requested_price"],
              "remaining_quantity": payload["remaining_quantity"]}
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": detail["event_month"], "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    return DeferredProjection(event, {**detail, "content_hash": _digest(detail)})


def recover_entry_reprice_deferred_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("order_management", "entry_reprice_deferred")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]):
        raise ValueError("Deferred reprice event/detail differs")
    return {"reason": "portfolio_allocation_capacity",
            "requested_price": detail["requested_price"],
            "remaining_quantity": detail["remaining_quantity"],
            "strategy_id": detail["strategy_id"],
            "strategy_revision": detail["strategy_revision"],
            "correlation_id": event["correlation_id"],
            "causation_id": event["causation_id"]}


def seal_entry_reprice_deferred_v3(
    details: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    by_id = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(by_id) != len(events):
        raise ValueError("Deferred reprice parent event repeats")
    required = {key for key, row in by_id.items() if
                (row["category"], row["entity_type"]) ==
                ("order_management", "entry_reprice_deferred")}
    found = set()
    hashes = []
    fields = {name for name, _ in DEFERRED.columns}
    for detail in details:
        if set(detail) != fields:
            raise ValueError("Deferred reprice detail columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = by_id.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or detail["event_month"] != event["event_month"]):
            raise ValueError("Deferred reprice event/detail identity differs")
        found.add(identity)
        payload = recover_entry_reprice_deferred_payload(event, detail)
        if (not event["entity_id"] or not detail["account_id"]
                or not isinstance(payload["strategy_id"], str)
                or not payload["strategy_id"]
                or type(payload["strategy_revision"]) is not int
                or not 0 <= payload["strategy_revision"] < 2**32
                or any(type(payload[key]) is not float or not math.isfinite(payload[key])
                       for key in ("requested_price", "remaining_quantity"))
                or payload["requested_price"] <= 0
                or payload["remaining_quantity"] < 0):
            raise ValueError("Deferred reprice scalar evidence differs")
        digest = _digest({key: value for key, value in detail.items()
                          if key != "content_hash"})
        if digest != detail["content_hash"]:
            raise ValueError("Deferred reprice detail hash differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Deferred reprice parent lacks exact typed child")
    return {"entry_reprice_deferred_count": len(details),
            "entry_reprice_deferred_hash": _digest(sorted(hashes))}
