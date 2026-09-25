"""Pure, inactive normalized projection of Portfolio reconciliation evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
from typing import Any, Mapping
from uuid import UUID, NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.portfolio import PortfolioReconciliationDifference


PARENT = TableContract(
    "trading_portfolio_reconciliation_event_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"), ("account_key", "String"),
     ("snapshot_id", "String"), ("difference_count", "UInt32"),
     ("difference_hash", "FixedString(64)"),
     ("source_event_time", "DateTime64(6, 'UTC')"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,source_event_time,record_id")
CHILD = TableContract(
    "trading_portfolio_reconciliation_difference_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("parent_record_id", "UUID"),
     ("account_id", "String"), ("ordinal", "UInt16"),
     ("account_key", "String"), ("ticker", "String"),
     ("broker_quantity", "Float64"), ("attributed_quantity", "Float64"),
     ("unattributed_quantity", "Float64"),
     ("observed_at", "DateTime64(6, 'UTC')"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,parent_record_id,ordinal,record_id")


@dataclass(frozen=True, slots=True)
class ReconciliationV3Projection:
    event: Mapping[str, Any]
    parent: Mapping[str, Any]
    differences: tuple[Mapping[str, Any], ...]


def _digest(row: Mapping[str, Any]) -> str:
    return sha256(canonical_json(row).encode("utf-8")).hexdigest()


def _utc(value: Any) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Reconciliation time must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def project_reconciliation_v3(record: JournalRecord, *, attempt_id: str,
                              batch_id: str,
                              account_key: str,
                              snapshot_observed_at: datetime) -> ReconciliationV3Projection:
    """Reject any loss, duplicate, mixed account, or noncausal child evidence."""
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "portfolio_reconciliation")
            or not record.run_id or not record.account_id or not account_key
            or record.entity_id != account_key):
        raise ValueError("Reconciliation event identity is invalid")
    payload = record.payload
    if (not isinstance(payload, Mapping)
            or set(payload) != {"event", "snapshot_id", "difference_count", "differences",
                                "correlation_id", "causation_id"}
            or any(not isinstance(payload[key], str) or not payload[key]
                   for key in ("correlation_id", "causation_id"))
            or payload["event"] != "portfolio_reconciliation_completed"
            or not isinstance(payload["snapshot_id"], str)
            or not payload["snapshot_id"]
            or type(payload["difference_count"]) is not int
            or not 0 <= payload["difference_count"] <= 65535
            or not isinstance(payload["differences"], list)
            or len(payload["differences"]) != payload["difference_count"]):
        raise ValueError("Reconciliation payload is not an exact completed snapshot")
    event_at = _utc(record.event_time)
    _utc(record.recorded_at)
    source_at = _utc(snapshot_observed_at)
    if source_at > event_at:
        raise ValueError("Reconciliation snapshot is from the future")
    month = event_at[:7] + "-01"
    expected_fields = set(PortfolioReconciliationDifference.__dataclass_fields__)
    rows: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    for raw in payload["differences"]:
        if not isinstance(raw, Mapping) or set(raw) != expected_fields:
            raise ValueError("Reconciliation difference has missing or extra fields")
        key = (raw["account_key"], raw["ticker"])
        if (not all(isinstance(part, str) and part for part in key)
                or key[0] != account_key or key in keys):
            raise ValueError("Reconciliation difference is mixed or duplicate")
        keys.add(key)
        observed = _utc(raw["observed_at"])
        if observed != source_at:
            raise ValueError("Reconciliation difference differs from snapshot time")
        numeric = {}
        for field in ("broker_quantity", "attributed_quantity", "unattributed_quantity"):
            value = raw[field]
            if type(value) is not float or not math.isfinite(value):
                raise ValueError("Reconciliation quantity is not finite Float64")
            numeric[field] = float(value)
        if not math.isclose(
                numeric["broker_quantity"] - numeric["attributed_quantity"],
                numeric["unattributed_quantity"], rel_tol=0, abs_tol=1e-9):
            raise ValueError("Reconciliation difference arithmetic differs")
        rows.append({"account_key": key[0], "ticker": key[1],
                     **numeric, "observed_at": observed})
    rows.sort(key=lambda row: (row["account_key"], row["ticker"]))
    # The producer sorts its full account population; reordering here would
    # hide source corruption and change the original journal payload.
    if [row["ticker"] for row in rows] != [row["ticker"] for row in payload["differences"]]:
        raise ValueError("Reconciliation differences are not in canonical order")
    common = {"run_id": record.run_id, "event_month": month,
              "batch_id": batch_id, "account_id": record.account_id}
    children = []
    for ordinal, row in enumerate(rows):
        child = {**common,
                 "record_id": str(uuid5(NAMESPACE_URL, f"{record.record_id}:difference:{ordinal}")),
                 "parent_record_id": record.record_id, "ordinal": ordinal, **row}
        children.append({**child, "content_hash": _digest(child)})
    parent = {**common, "record_id": record.record_id,
              "account_key": account_key, "snapshot_id": payload["snapshot_id"],
              "difference_count": len(children),
              "difference_hash": _digest(children),
              "source_event_time": source_at}
    event = {**common, "attempt_id": attempt_id, "record_id": record.record_id,
             "sequence": record.sequence, "event_time": event_at,
             "recorded_at": _utc(record.recorded_at),
             "category": record.category, "entity_type": record.entity_type,
             "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    return ReconciliationV3Projection(
        event, {**parent, "content_hash": _digest(parent)}, tuple(children))
