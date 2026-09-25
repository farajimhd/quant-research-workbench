"""Inactive typed contract for the two emitted short-order refusal variants."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


SHORT_ORDER_SKIP = TableContract(
    "trading_broker_short_order_skip_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("strategy_id", "String"), ("strategy_revision", "UInt32"),
     ("ticker", "String"), ("reason", "LowCardinality(String)"),
     ("required_shares", "Nullable(Float64)"),
     ("required_shares_was_int", "Nullable(UInt8)"),
     ("available_shares", "Nullable(Float64)"),
     ("available_shares_was_int", "Nullable(UInt8)"),
     ("classification", "Nullable(String)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id",
)


@dataclass(frozen=True, slots=True)
class ShortabilityProjection:
    event: Mapping[str, Any]
    detail: Mapping[str, Any]


def _utc(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Shortability event timestamp lacks UTC authority")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _number(value: Any) -> tuple[float, int]:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Shortability quantity is not a finite scalar")
    number = float(value)
    if not math.isfinite(number) or (type(value) is int and int(number) != value):
        raise ValueError("Shortability quantity cannot round-trip Float64")
    return number, int(type(value) is int)


def project_short_order_skip_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
) -> ShortabilityProjection:
    """Reject extra evidence; omit only fixed-key broker fields derivable exactly."""
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("broker_policy", "short_order_skipped")
            or not record.run_id or not record.entity_id):
        raise ValueError("Shortability journal identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Shortability payload is not a mapping")
    common = {"reason", "ticker", "strategy_id", "strategy_revision",
              "correlation_id", "causation_id"}
    if (not all(isinstance(payload.get(key), str) and payload[key]
                for key in ("reason", "ticker", "strategy_id",
                            "correlation_id", "causation_id"))
            or type(payload.get("strategy_revision")) is not int
            or not 0 <= payload["strategy_revision"] < 2**32):
        raise ValueError("Shortability common evidence differs")
    detail = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": _utc(record.event_time)[:7] + "-01",
              "batch_id": batch_id, "account_id": record.account_id,
              "strategy_id": payload["strategy_id"],
              "strategy_revision": payload["strategy_revision"],
              "ticker": payload["ticker"], "reason": payload["reason"],
              "required_shares": None, "required_shares_was_int": None,
              "available_shares": None, "available_shares_was_int": None,
              "classification": None}
    if payload["reason"] == "shortability_provider_unavailable":
        if set(payload) != common or record.account_id:
            raise ValueError("Unavailable shortability evidence differs")
    elif payload["reason"] == "insufficient_or_unavailable_borrow":
        fields = common | {"required_shares", "available_shares",
                           "classification", "ibkr_fields"}
        if (set(payload) != fields or not record.account_id
                or not isinstance(payload["classification"], str)
                or not isinstance(payload["ibkr_fields"], Mapping)
                or set(payload["ibkr_fields"]) != {"7636", "7644"}
                or payload["ibkr_fields"]["7636"] != payload["available_shares"]
                or type(payload["ibkr_fields"]["7636"]) is not
                   type(payload["available_shares"])
                or payload["ibkr_fields"]["7644"] != payload["classification"]):
            raise ValueError("Borrow evidence is not exact fixed-key source")
        required, required_int = _number(payload["required_shares"])
        available, available_int = _number(payload["available_shares"])
        detail.update(required_shares=required,
                      required_shares_was_int=required_int,
                      available_shares=available,
                      available_shares_was_int=available_int,
                      classification=payload["classification"])
    else:
        raise ValueError("Shortability reason has no closed typed contract")
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": detail["event_month"], "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    return ShortabilityProjection(event, {**detail, "content_hash": sha256(
        canonical_json(detail).encode("utf-8")).hexdigest()})


def recover_short_order_skip_payload(
    event: Mapping[str, Any], detail: Mapping[str, Any],
) -> dict[str, Any]:
    """Losslessly reconstruct the source payload from sealed scalar facts."""
    if (event["record_id"] != detail["record_id"]
            or event["account_id"] != detail["account_id"]
            or event["category"] != "broker_policy"
            or event["entity_type"] != "short_order_skipped"):
        raise ValueError("Shortability event/detail identity differs")
    payload = {"reason": detail["reason"], "ticker": detail["ticker"],
               "strategy_id": detail["strategy_id"],
               "strategy_revision": detail["strategy_revision"],
               "correlation_id": event["correlation_id"],
               "causation_id": event["causation_id"]}
    if detail["reason"] == "shortability_provider_unavailable":
        if any(detail[key] is not None for key in (
                "required_shares", "required_shares_was_int",
                "available_shares", "available_shares_was_int", "classification")):
            raise ValueError("Unavailable shortability has borrow facts")
    elif detail["reason"] == "insufficient_or_unavailable_borrow":
        for key in ("required_shares", "required_shares_was_int",
                    "available_shares", "available_shares_was_int", "classification"):
            if detail[key] is None:
                raise ValueError("Borrow facts are incomplete")
        if (not isinstance(detail["classification"], str)
                or any(type(detail[key]) not in (int, float)
                       or not math.isfinite(detail[key])
                       for key in ("required_shares", "available_shares"))
                or any(type(detail[key]) is not int or detail[key] not in (0, 1)
                       for key in ("required_shares_was_int",
                                   "available_shares_was_int"))
                or any(detail[quantity + "_was_int"]
                       and int(detail[quantity]) != detail[quantity]
                       for quantity in ("required_shares", "available_shares"))):
            raise ValueError("Borrow scalar type witness differs")
        required = (int(detail["required_shares"])
                    if detail["required_shares_was_int"] else
                    float(detail["required_shares"]))
        available = (int(detail["available_shares"])
                     if detail["available_shares_was_int"] else
                     float(detail["available_shares"]))
        payload.update(required_shares=required, available_shares=available,
                       classification=detail["classification"],
                       ibkr_fields={"7636": available,
                                    "7644": detail["classification"]})
    else:
        raise ValueError("Shortability reason has no closed typed contract")
    return payload


def seal_short_order_skip_v3(
    details: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    events: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    """Require one exact child for each shortability parent in a V3 batch."""
    batch = str(UUID(batch_id))
    parents = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(parents) != len(events):
        raise ValueError("Shortability parent event repeats")
    required = {key for key, row in parents.items() if
                (row["category"], row["entity_type"]) ==
                ("broker_policy", "short_order_skipped")}
    fields = {name for name, _ in SHORT_ORDER_SKIP.columns}
    found: set[str] = set()
    identities: list[tuple[str, str]] = []
    for detail in details:
        if set(detail) != fields:
            raise ValueError("Shortability child columns differ")
        identity = str(UUID(str(detail["record_id"])))
        parent = parents.get(identity)
        if identity in found or identity not in required or parent is None:
            raise ValueError("Shortability child parent identity differs")
        found.add(identity)
        if (detail["run_id"] != run_id or parent["run_id"] != run_id
                or str(UUID(str(detail["batch_id"]))) != batch
                or str(UUID(str(parent["batch_id"]))) != batch
                or detail["event_month"] != parent["event_month"]
                or detail["account_id"] != parent["account_id"]
                or parent["entity_id"] == ""):
            raise ValueError("Shortability child differs from event")
        payload = recover_short_order_skip_payload(parent, detail)
        if (payload["reason"] == "shortability_provider_unavailable"
                and detail["account_id"]):
            raise ValueError("Unavailable shortability account differs")
        if (payload["reason"] == "insufficient_or_unavailable_borrow"
                and not detail["account_id"]):
            raise ValueError("Borrow account is absent")
        content = {key: value for key, value in detail.items()
                   if key != "content_hash"}
        digest = sha256(canonical_json(content).encode("utf-8")).hexdigest()
        if detail["content_hash"] != digest:
            raise ValueError("Shortability child content hash differs")
        identities.append((identity, digest))
    if found != required:
        raise ValueError("Shortability parent lacks exact child")
    return {"broker_short_order_skip_count": len(details),
            "broker_short_order_skip_hash": sha256(canonical_json(
                sorted(identities)).encode("utf-8")).hexdigest()}
