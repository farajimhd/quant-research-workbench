"""Closed V3 scalar evidence for OMS protected-exit position reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.journal_decimal import decimal_38_18, source_float_from_decimal


SNAPSHOT = TableContract(
    "trading_protected_exit_snapshot_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("ticker", "LowCardinality(String)"), ("attempt", "UInt8"),
     ("requested_quantity", "Decimal(38, 18)"),
     ("broker_position_quantity", "Decimal(38, 18)"),
     ("executable_remaining_quantity", "Decimal(38, 18)"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
REASON = "protected_order_became_terminal_before_modify"


@dataclass(frozen=True, slots=True)
class SnapshotProjection:
    event: dict[str, Any]
    detail: dict[str, Any]


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Protected exit snapshot time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _validate_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != {"ticker", "attempt", "requested_quantity",
                        "broker_position_quantity", "executable_remaining_quantity",
                        "reason", "strategy_id", "strategy_revision",
                        "correlation_id", "causation_id"}:
        raise ValueError("Protected exit snapshot has unmodeled source evidence")
    if (type(payload["ticker"]) is not str or not payload["ticker"]
            or payload["ticker"] != payload["ticker"].upper()
            or type(payload["attempt"]) is not int
            or payload["attempt"] not in (1, 2, 3)
            or payload["reason"] != REASON
            or type(payload["strategy_revision"]) is not int
            or not 0 <= payload["strategy_revision"] <= 0xFFFFFFFF
            or any(type(payload[key]) is not str or not payload[key]
                   for key in ("strategy_id", "correlation_id", "causation_id"))):
        raise ValueError("Protected exit snapshot source identity differs")
    decimal_38_18(payload["requested_quantity"], source_float=True, positive=True)
    decimal_38_18(payload["broker_position_quantity"], source_float=True)
    decimal_38_18(payload["executable_remaining_quantity"],
                  source_float=True, nonnegative=True)


def project_protected_exit_snapshot_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> SnapshotProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("order_management", "protected_exit_snapshot_reconciled")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Protected exit snapshot event identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Protected exit snapshot payload differs")
    _validate_payload(payload)
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
              "account_id": record.account_id, "ticker": payload["ticker"],
              "attempt": payload["attempt"],
              "requested_quantity": decimal_38_18(payload["requested_quantity"],
                                                     source_float=True, positive=True),
              "broker_position_quantity": decimal_38_18(
                  payload["broker_position_quantity"], source_float=True),
              "executable_remaining_quantity": decimal_38_18(
                  payload["executable_remaining_quantity"], source_float=True,
                  nonnegative=True),
              "strategy_id": payload["strategy_id"],
              "strategy_revision": payload["strategy_revision"]}
    return SnapshotProjection(event, {**detail, "content_hash": _hash(detail)})


def recover_protected_exit_snapshot_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("order_management", "protected_exit_snapshot_reconciled")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]
            or not event["entity_id"]):
        raise ValueError("Protected exit snapshot parent/detail identity differs")
    payload = {"ticker": detail["ticker"], "attempt": detail["attempt"],
               "requested_quantity": source_float_from_decimal(
                   detail["requested_quantity"], positive=True),
               "broker_position_quantity": source_float_from_decimal(
                   detail["broker_position_quantity"]),
               "executable_remaining_quantity": source_float_from_decimal(
                   detail["executable_remaining_quantity"], nonnegative=True),
               "reason": REASON, "strategy_id": detail["strategy_id"],
               "strategy_revision": detail["strategy_revision"],
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    _validate_payload(payload)
    return payload


def seal_protected_exit_snapshot_v3(
    details: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Protected exit snapshot parent event repeats")
    required = {identity for identity, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("order_management", "protected_exit_snapshot_reconciled")}
    found: set[str] = set()
    hashes: list[tuple[str, str]] = []
    for detail in details:
        if set(detail) != {name for name, _ in SNAPSHOT.columns}:
            raise ValueError("Protected exit snapshot columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = parents.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or detail["event_month"] != event["event_month"]):
            raise ValueError("Protected exit snapshot parent identity differs")
        found.add(identity)
        recover_protected_exit_snapshot_payload(event, detail)
        canonical = {key: value for key, value in detail.items() if key != "content_hash"}
        for field, constraints in (("requested_quantity", {"positive": True}),
                                   ("broker_position_quantity", {}),
                                   ("executable_remaining_quantity", {"nonnegative": True})):
            canonical[field] = decimal_38_18(canonical[field], **constraints)
        digest = _hash(canonical)
        if detail["content_hash"] != digest:
            raise ValueError("Protected exit snapshot hash differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Protected exit snapshot parent lacks exact typed child")
    return {"protected_exit_snapshot_count": len(details),
            "protected_exit_snapshot_hash": _hash(sorted(hashes))}
