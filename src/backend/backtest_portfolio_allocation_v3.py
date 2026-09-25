"""Closed V3 fill-applied Portfolio allocation evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.journal_decimal import decimal_38_18, source_float_from_decimal


ALLOCATION = TableContract(
    "trading_portfolio_allocation_fill_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("strategy_id", "String"), ("assignment_id", "String"),
     ("ticker", "LowCardinality(String)"), ("action", "LowCardinality(String)"),
     ("incremental_quantity", "Decimal(38, 18)"),
     ("quantity", "Decimal(38, 18)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)
_ACTIONS = frozenset({"enter_long", "add_long", "enter_short", "add_short",
                      "reduce_long", "take_profit", "exit", "reduce_short", "cover"})


@dataclass(frozen=True, slots=True)
class AllocationProjection:
    event: dict[str, Any]
    detail: dict[str, Any]


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Portfolio allocation time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _validate_payload(payload: Mapping[str, Any], *, entity_id: str,
                      account_id: str) -> None:
    if set(payload) != {"event", "incremental_quantity", "quantity", "ticker",
                        "action", "strategy_id", "assignment_id",
                        "correlation_id", "causation_id"}:
        raise ValueError("Portfolio allocation has unmodeled source evidence")
    if (payload["event"] != "allocation_fill_applied"
            or type(payload["ticker"]) is not str or not payload["ticker"]
            or payload["ticker"] != payload["ticker"].upper()
            or payload["action"] not in _ACTIONS
            or type(payload["assignment_id"]) is not str
            or any(type(payload[key]) is not str or not payload[key]
                   for key in ("strategy_id", "correlation_id", "causation_id"))):
        raise ValueError("Portfolio allocation source identity differs")
    expected = (f"{account_id}:{payload['strategy_id']}:"
                f"{payload['assignment_id'] or payload['ticker']}:{payload['ticker']}")
    if entity_id != expected:
        raise ValueError("Portfolio allocation id differs from source constituents")
    decimal_38_18(payload["incremental_quantity"], source_float=True)
    decimal_38_18(payload["quantity"], source_float=True)


def project_portfolio_allocation_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> AllocationProjection:
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "portfolio_allocation")
            or not record.run_id or not record.account_id or not record.entity_id):
        raise ValueError("Portfolio allocation event identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Portfolio allocation payload differs")
    _validate_payload(payload, entity_id=record.entity_id,
                      account_id=record.account_id)
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
              "strategy_id": payload["strategy_id"],
              "assignment_id": payload["assignment_id"],
              "ticker": payload["ticker"], "action": payload["action"],
              "incremental_quantity": decimal_38_18(
                  payload["incremental_quantity"], source_float=True),
              "quantity": decimal_38_18(payload["quantity"], source_float=True)}
    return AllocationProjection(event, {**detail, "content_hash": _hash(detail)})


def recover_portfolio_allocation_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    if ((event["category"], event["entity_type"]) !=
            ("portfolio_management", "portfolio_allocation")
            or event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]):
        raise ValueError("Portfolio allocation parent/detail identity differs")
    payload = {"event": "allocation_fill_applied",
               "incremental_quantity": source_float_from_decimal(
                   detail["incremental_quantity"]),
               "quantity": source_float_from_decimal(detail["quantity"]),
               "ticker": detail["ticker"], "action": detail["action"],
               "strategy_id": detail["strategy_id"],
               "assignment_id": detail["assignment_id"],
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    _validate_payload(payload, entity_id=event["entity_id"],
                      account_id=event["account_id"])
    return payload


def seal_portfolio_allocation_v3(
    details: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Portfolio allocation parent event repeats")
    required = {identity for identity, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("portfolio_management", "portfolio_allocation")}
    found: set[str] = set()
    hashes: list[tuple[str, str]] = []
    for detail in details:
        if set(detail) != {name for name, _ in ALLOCATION.columns}:
            raise ValueError("Portfolio allocation columns differ")
        identity = str(UUID(str(detail["record_id"])))
        event = parents.get(identity)
        if (identity in found or identity not in required or event is None
                or detail["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or detail["account_id"] != event["account_id"]
                or detail["event_month"] != event["event_month"]):
            raise ValueError("Portfolio allocation parent identity differs")
        found.add(identity)
        recover_portfolio_allocation_payload(event, detail)
        canonical = {key: value for key, value in detail.items() if key != "content_hash"}
        for field in ("incremental_quantity", "quantity"):
            canonical[field] = decimal_38_18(canonical[field])
        digest = _hash(canonical)
        if detail["content_hash"] != digest:
            raise ValueError("Portfolio allocation hash differs")
        hashes.append((identity, digest))
    if found != required:
        raise ValueError("Portfolio allocation parent lacks exact typed child")
    return {"portfolio_allocation_fill_count": len(details),
            "portfolio_allocation_fill_hash": _hash(sorted(hashes))}
