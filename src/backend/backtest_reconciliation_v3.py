"""Closed V3 projection and child seal for Portfolio reconciliation evidence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import math
import re
from typing import Any, Mapping, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.portfolio import PortfolioReconciliationDifference


CHILD = TableContract(
    "trading_portfolio_reconciliation_difference_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("parent_record_id", "UUID"),
     ("ordinal", "UInt16"), ("ticker", "String"),
     ("broker_quantity", "Float64"), ("attributed_quantity", "Float64"),
     ("unattributed_quantity", "Float64"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,parent_record_id,ordinal,record_id")


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
                              account_key: str) -> ReconciliationV3Projection:
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
            or set(payload) != {"event", "snapshot_id", "snapshot_observed_at",
                                "difference_count", "differences",
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
    source_at = _utc(payload["snapshot_observed_at"])
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
        child = {"run_id": record.run_id, "event_month": month,
                 "batch_id": batch_id,
                 "record_id": str(uuid5(NAMESPACE_URL, f"{record.record_id}:difference:{ordinal}")),
                 "parent_record_id": record.record_id, "ordinal": ordinal,
                 "ticker": row["ticker"],
                 "broker_quantity": row["broker_quantity"],
                 "attributed_quantity": row["attributed_quantity"],
                 "unattributed_quantity": row["unattributed_quantity"]}
        children.append({**child, "content_hash": _digest(child)})
    parent = {**common, "record_id": record.record_id,
              "account_key": account_key, "snapshot_id": payload["snapshot_id"],
              "difference_count": len(children),
              "difference_hash": _digest(payload["differences"]),
              "source_event_time": source_at}
    event = {**common, "attempt_id": attempt_id, "record_id": record.record_id,
             "sequence": record.sequence, "event_time": event_at,
             "recorded_at": _utc(record.recorded_at),
             "category": record.category, "entity_type": record.entity_type,
             "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    # The existing V1 reconciliation detail is the sole normalized parent.
    # Its content hash is assigned by the common typed-family writer.
    return ReconciliationV3Projection(event, parent, tuple(children))


def _stored_time(value: Any, *, stored_utc: bool) -> datetime:
    if isinstance(value, datetime):
        at = value
    elif isinstance(value, str):
        source = value.replace("Z", "+00:00")
        nanos = re.fullmatch(
            r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d{6}(\d{3})",
            source)
        if stored_utc and nanos is not None and nanos.group(1) != "000":
            raise ValueError("Reconciliation timestamp exceeds source precision")
        at = datetime.fromisoformat(source)
    else:
        raise ValueError("Reconciliation timestamp is not scalar")
    if at.tzinfo is None and stored_utc:
        at = at.replace(tzinfo=timezone.utc)
    if at.tzinfo is None:
        raise ValueError("Reconciliation timestamp lacks UTC authority")
    return at.astimezone(timezone.utc)


def seal_reconciliation_difference_family_v3(
    differences: Sequence[Mapping[str, Any]],
    parent_events: Sequence[Mapping[str, Any]],
    parent_reconciliations: Sequence[Mapping[str, Any]],
    *, run_id: str, batch_id: str, stored_utc: bool = False,
) -> dict[str, Any]:
    """Seal every ordered child against the already-sealed V1 parent family."""
    batch = str(UUID(batch_id))
    events = {str(UUID(str(row["record_id"]))): row for row in parent_events}
    parents = {str(UUID(str(row["record_id"]))): row
               for row in parent_reconciliations}
    if len(events) != len(parent_events) or len(parents) != len(parent_reconciliations):
        raise ValueError("Reconciliation parent identity repeats")
    expected_parent_ids = {record_id for record_id, row in events.items()
                           if (row["category"], row["entity_type"]) ==
                           ("portfolio_management", "portfolio_reconciliation")}
    if set(parents) != expected_parent_ids:
        raise ValueError("Reconciliation parent detail is incomplete")
    grouped: dict[str, list[dict[str, Any]]] = {}
    identities: list[tuple[str, str]] = []
    child_fields = {name for name, _ in CHILD.columns}
    for raw in differences:
        if set(raw) != child_fields:
            raise ValueError("Reconciliation difference row differs from schema")
        row = dict(raw)
        identity = str(UUID(str(row["record_id"])))
        parent_id = str(UUID(str(row["parent_record_id"])))
        ordinal = row["ordinal"]
        if (type(ordinal) is not int or not 0 <= ordinal < 2**16
                or identity != str(uuid5(NAMESPACE_URL,
                    f"{parent_id}:difference:{ordinal}"))
                or row["run_id"] != run_id
                or str(UUID(str(row["batch_id"]))) != batch
                or parent_id not in parents):
            raise ValueError("Reconciliation child identity differs")
        quantities = {}
        for field in ("broker_quantity", "attributed_quantity",
                      "unattributed_quantity"):
            value = row[field]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Reconciliation child quantity is not finite")
            quantities[field] = float(value)
        if not math.isclose(quantities["broker_quantity"] -
                            quantities["attributed_quantity"],
                            quantities["unattributed_quantity"],
                            rel_tol=0, abs_tol=1e-9):
            raise ValueError("Reconciliation child arithmetic differs")
        normalized = {**row, **quantities, "record_id": identity,
                      "parent_record_id": parent_id, "batch_id": batch}
        content = {key: value for key, value in normalized.items()
                   if key != "content_hash"}
        if row["content_hash"] != _digest(content):
            raise ValueError("Reconciliation child content hash differs")
        grouped.setdefault(parent_id, []).append(normalized)
        identities.append((identity, row["content_hash"]))
    if len(identities) != len(set(identities)):
        raise ValueError("Reconciliation child repeats")
    for parent_id, parent in parents.items():
        event = events[parent_id]
        if (parent["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(parent["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or parent["account_id"] != event["account_id"]
                or parent["account_key"] != event["entity_id"]
                or parent["event_month"] != event["event_month"]):
            raise ValueError("Reconciliation parent differs from event")
        source_at = _stored_time(parent["source_event_time"],
                                 stored_utc=stored_utc)
        event_at = _stored_time(event["event_time"], stored_utc=stored_utc)
        if source_at > event_at:
            raise ValueError("Reconciliation snapshot is from the future")
        children = sorted(grouped.get(parent_id, []), key=lambda row: row["ordinal"])
        if (type(parent["difference_count"]) is not int
                or parent["difference_count"] != len(children)
                or [row["ordinal"] for row in children] != list(range(len(children)))
                or any(row["event_month"] != parent["event_month"]
                       for row in children)):
            raise ValueError("Reconciliation child set is incomplete")
        source_rows = [{"account_key": parent["account_key"],
                        "observed_at": source_at,
                        **{key: child[key] for key in (
                            "ticker", "broker_quantity", "attributed_quantity",
                            "unattributed_quantity")}}
                       for child in children]
        if (source_rows != sorted(source_rows, key=lambda row: row["ticker"])
                or parent["difference_hash"] != _digest(source_rows)):
            raise ValueError("Reconciliation difference hash differs from parent")
    return {
        "portfolio_reconciliation_difference_count": len(differences),
        "portfolio_reconciliation_difference_hash": _digest(sorted(identities)),
    }
