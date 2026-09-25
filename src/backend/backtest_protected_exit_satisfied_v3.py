"""Closed V3 evidence for an OMS protected exit satisfied before repricing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


SATISFIED = TableContract(
    "trading_protected_exit_satisfied_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("ticker", "LowCardinality(String)"), ("action", "LowCardinality(String)"),
     ("intent_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("reason_code", "LowCardinality(String)"),
     ("requested_quantity", "Decimal(38, 18)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
REASONS = frozenset({"protected_target_filled_before_exit_reprice",
                     "protected_target_filled_during_exit_reconciliation"})


@dataclass(frozen=True, slots=True)
class SatisfiedProjection:
    event: dict[str, Any]
    detail: dict[str, Any]


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Protected exit event time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _quantity(value: Any, *, source: bool) -> str:
    if (source and type(value) is not float) or (not source and type(value) not in (int, float)):
        if source or not isinstance(value, (str, Decimal)):
            raise ValueError("Protected exit quantity type differs")
    try:
        number = Decimal(str(value))
        with localcontext() as context:
            context.prec = 80
            exact = number.quantize(Decimal("0.000000000000000001"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Protected exit quantity cannot fit Decimal(38, 18)") from exc
    if (not exact.is_finite() or exact <= 0 or exact != number
            or exact >= Decimal("100000000000000000000")):
        raise ValueError("Protected exit quantity cannot fit Decimal(38, 18) losslessly")
    return format(exact, ".18f")


def _validate_payload(payload: Mapping[str, Any], *, entity_id: str) -> None:
    if set(payload) != {"order_group_id", "ticker", "action", "intent_id",
                        "strategy_id", "strategy_revision", "reason",
                        "requested_quantity", "correlation_id", "causation_id"}:
        raise ValueError("Protected exit has unmodeled source evidence")
    if (payload["order_group_id"] != entity_id or not isinstance(payload["ticker"], str)
            or not payload["ticker"] or payload["ticker"] != payload["ticker"].upper()
            or payload["action"] not in {"exit", "take_profit", "reduce_long",
                                           "reduce_short", "cover"}
            or payload["reason"] not in REASONS
            or type(payload["strategy_revision"]) is not int
            or not 0 <= payload["strategy_revision"] <= 0xFFFFFFFF
            or any(type(payload[key]) is not str or not payload[key]
                   for key in ("intent_id", "strategy_id", "correlation_id", "causation_id"))):
        raise ValueError("Protected exit source identity differs")
    _quantity(payload["requested_quantity"], source=False)


def project_protected_exit_satisfied_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> SatisfiedProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("order_management", "protected_exit_already_satisfied")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Protected exit event identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Protected exit payload differs")
    _validate_payload(payload, entity_id=record.entity_id)
    quantity = _quantity(payload["requested_quantity"], source=True)
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
              "action": payload["action"], "intent_id": payload["intent_id"],
              "strategy_id": payload["strategy_id"],
              "strategy_revision": payload["strategy_revision"],
              "reason_code": payload["reason"], "requested_quantity": quantity}
    return SatisfiedProjection(event, {**detail, "content_hash": _hash(detail)})


def recover_protected_exit_satisfied_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("order_management", "protected_exit_already_satisfied")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]):
        raise ValueError("Protected exit parent/detail identity differs")
    payload = {"order_group_id": event["entity_id"], "ticker": detail["ticker"],
               "action": detail["action"], "intent_id": detail["intent_id"],
               "strategy_id": detail["strategy_id"],
               "strategy_revision": detail["strategy_revision"],
               "reason": detail["reason_code"],
               "requested_quantity": float(_quantity(detail["requested_quantity"], source=False)),
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    _validate_payload(payload, entity_id=event["entity_id"])
    return payload


def seal_protected_exit_satisfied_v3(
    details: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Protected exit parent event repeats")
    required = {key for key, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("order_management", "protected_exit_already_satisfied")}
    found: set[str] = set()
    hashes: list[tuple[str, str]] = []
    for detail in details:
        if set(detail) != {name for name, _ in SATISFIED.columns}:
            raise ValueError("Protected exit detail columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = parents.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or detail["event_month"] != event["event_month"]):
            raise ValueError("Protected exit parent identity differs")
        found.add(identity)
        recover_protected_exit_satisfied_payload(event, detail)
        canonical = {key: value for key, value in detail.items() if key != "content_hash"}
        canonical["requested_quantity"] = _quantity(canonical["requested_quantity"], source=False)
        digest = _hash(canonical)
        if detail["content_hash"] != digest:
            raise ValueError("Protected exit detail hash differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Protected exit parent lacks exact typed child")
    return {"protected_exit_satisfied_count": len(details),
            "protected_exit_satisfied_hash": _hash(sorted(hashes))}
