"""Closed, scalar V3 projection for Portfolio reservation reasons.

This is staging code. Its rows must not be published until the V3 commit fence
includes their count/hash and the cold reader verifies them. V1/V2 remain
unchanged, and no reason is stored as JSON or an array.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from src.backend.backtest_squeeze_episode_schema import RESERVATION_REASON
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.portfolio import PortfolioReservation


_ROW_COLUMNS = {name for name, _ in RESERVATION_REASON.columns}
_RESERVATION_FIELDS = {field.name for field in fields(PortfolioReservation)}
_LINEAGE = {"correlation_id", "causation_id"}


def project_reservation_reasons_v3(
    record: JournalRecord, *, batch_id: str,
) -> tuple[dict[str, Any], ...]:
    """Return one ordered child per non-redundant reason in a real emitter fact."""
    if (record.category, record.entity_type) != (
        "portfolio_management", "portfolio_reservation"
    ):
        raise ValueError("Not a Portfolio reservation fact")
    payload = record.payload
    event = payload.get("event")
    if event == "reservation_released":
        expected = _RESERVATION_FIELDS | _LINEAGE | {"event", "reason"}
        reasons = (payload.get("reason"),)
    elif event == "entry_reprice_authorized":
        expected = _RESERVATION_FIELDS | _LINEAGE | {
            "event", "price", "reasons"
        }
        values = payload.get("reasons")
        if type(values) is not list:
            raise ValueError("Reservation reprice reasons must be an ordered list")
        reasons = tuple(values)
        try:
            price = Decimal(str(payload["price"]))
            reference = Decimal(str(payload["reference_price"]))
        except (InvalidOperation, KeyError, ValueError) as exc:
            raise ValueError("Reservation reprice price is invalid") from exc
        if not price.is_finite() or price != reference:
            raise ValueError("Reservation reprice price differs from reserved price")
    elif event in {
        "reservation_created", "cash_tranche_budget_reserved", "reservation_updated"
    }:
        expected = _RESERVATION_FIELDS | _LINEAGE | {"event"}
        reasons = ()
    else:
        raise ValueError("Reservation event is not a known V3 lifecycle transition")
    # Lineage is optional in the actual emitters; everything else is exact.
    if (set(payload) - _LINEAGE != expected - _LINEAGE
            or set(payload) - expected
            or payload.get("reservation_id") != record.entity_id
            or payload.get("account_id") != record.account_id
            or not record.account_id or record.event_time.tzinfo is None
            or len(reasons) >= 2**16
            or any(type(reason) is not str or not reason for reason in reasons)):
        raise ValueError("Reservation reason payload is incomplete or redundant")
    batch = str(UUID(batch_id))
    parent = str(UUID(record.record_id))
    month = record.event_time.astimezone(timezone.utc).strftime("%Y-%m-01")
    result = []
    for ordinal, reason in enumerate(reasons):
        content = {
            "record_id": str(uuid5(NAMESPACE_URL,
                                   f"arte-v3-reservation-reason:{parent}:{ordinal}")),
            "run_id": record.run_id,
            "event_month": month,
            "batch_id": batch,
            "parent_record_id": parent,
            "account_id": record.account_id,
            "ordinal": ordinal,
            "reason": reason,
        }
        if set(content) != _ROW_COLUMNS - {"content_hash"}:
            raise AssertionError("Reservation reason projection differs from table contract")
        result.append({**content,
                       "content_hash": sha256(canonical_json(content).encode()).hexdigest()})
    return tuple(result)


def seal_reservation_reason_family_v3(
    rows: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
    parent_reservations: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    """Validate every child and return its exact V3 count/hash fence fields.

    This pure seal is deliberately not a publisher. A V3 writer must persist
    the rows and verify them before committing these fields; cold readers must
    re-run the same seal on durable rows before exposing a prefix.
    """
    batch = str(UUID(batch_id))
    events = {str(UUID(str(row["record_id"]))): row for row in parent_events}
    reservations = {str(UUID(str(row["record_id"]))): row
                    for row in parent_reservations}
    if (len(events) != len(parent_events)
            or len(reservations) != len(parent_reservations)):
        raise ValueError("Reservation reason parent identity repeats")
    by_parent: dict[str, list[Mapping[str, Any]]] = {}
    identities: list[tuple[str, str]] = []
    for row in rows:
        if set(row) != _ROW_COLUMNS:
            raise ValueError("Reservation reason row differs from typed schema")
        record_id = str(UUID(str(row["record_id"])))
        parent_id = str(UUID(str(row["parent_record_id"])))
        ordinal = row["ordinal"]
        event = events.get(parent_id)
        reservation = reservations.get(parent_id)
        content = {key: value for key, value in row.items() if key != "content_hash"}
        if (type(ordinal) is not int or not 0 <= ordinal < 2**16
                or not isinstance(row["reason"], str) or not row["reason"]
                or not isinstance(row["content_hash"], str)
                or record_id != str(uuid5(NAMESPACE_URL,
                    f"arte-v3-reservation-reason:{parent_id}:{ordinal}"))
                or row["run_id"] != run_id or str(UUID(str(row["batch_id"]))) != batch
                or event is None or reservation is None
                or (event["category"], event["entity_type"]) != (
                    "portfolio_management", "portfolio_reservation")
                or event["run_id"] != run_id
                or str(UUID(str(event["batch_id"]))) != batch
                or str(reservation["run_id"]) != run_id
                or str(UUID(str(reservation["batch_id"]))) != batch
                or row["event_month"] != event["event_month"]
                or row["event_month"] != reservation["event_month"]
                or row["account_id"] != event["account_id"]
                or row["account_id"] != reservation["account_id"]
                or sha256(canonical_json(content).encode()).hexdigest()
                   != row["content_hash"]):
            raise ValueError("Reservation reason differs from its parent or hash")
        by_parent.setdefault(parent_id, []).append(row)
        identities.append((record_id, row["content_hash"]))
    if len(identities) != len(set(identities)):
        raise ValueError("Reservation reason identity repeats")
    for parent_id, reservation in reservations.items():
        event_name = reservation["event"]
        children = by_parent.get(parent_id, [])
        if (event_name not in {
                "reservation_created", "cash_tranche_budget_reserved",
                "reservation_updated", "reservation_released",
                "entry_reprice_authorized"}
                or event_name == "reservation_released" and len(children) != 1
                or event_name not in {"reservation_released", "entry_reprice_authorized"}
                   and children
                or sorted(row["ordinal"] for row in children)
                   != list(range(len(children)))):
            raise ValueError("Reservation lifecycle reason set is incomplete")
    return {
        "portfolio_reservation_reason_count": len(rows),
        "portfolio_reservation_reason_hash": sha256(
            canonical_json(sorted(identities)).encode()).hexdigest(),
    }
