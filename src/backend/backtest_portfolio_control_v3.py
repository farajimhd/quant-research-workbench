"""Inactive, closed typed projection for scalar Portfolio control events."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import JournalRecord, canonical_json
from src.trading_runtime.portfolio import PortfolioControlMode


CONTROL = TableContract(
    "trading_portfolio_control_v3",
    (("record_id", "UUID"), ("run_id", "String"), ("event_month", "Date"),
     ("batch_id", "UUID"), ("account_id", "String"),
     ("account_key", "String"), ("control_event", "LowCardinality(String)"),
     ("control_mode", "Nullable(String)"),
     ("strategy_id", "Nullable(String)"), ("enabled", "Nullable(UInt8)"),
     ("policy_hash", "Nullable(FixedString(64))"),
     ("entries_paused", "Nullable(UInt8)"),
     ("reason", "String"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id,account_id,record_id")


@dataclass(frozen=True, slots=True)
class ControlProjection:
    event: Mapping[str, Any]
    detail: Mapping[str, Any]


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Portfolio control timestamp lacks UTC authority")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def project_portfolio_control_v3(
    record: JournalRecord, *, attempt_id: str, batch_id: str,
    account_key: str,
) -> ControlProjection:
    """Project only the exact two scalar Portfolio-emitted variants."""
    UUID(record.record_id)
    UUID(attempt_id)
    UUID(batch_id)
    if ((record.category, record.entity_type) !=
            ("portfolio_management", "portfolio_control")
            or not record.run_id or not record.account_id or not account_key
            or not record.entity_id):
        raise ValueError("Portfolio control identity differs")
    payload = record.payload
    if not isinstance(payload, Mapping):
        raise ValueError("Portfolio control payload is not a mapping")
    kind = payload.get("event")
    lineage = {"correlation_id", "causation_id"}
    if (not all(isinstance(payload.get(key), str) and payload[key]
                for key in lineage)):
        raise ValueError("Portfolio control lineage is missing")
    detail = {"record_id": record.record_id, "run_id": record.run_id,
              "event_month": _utc(record.event_time)[:7] + "-01",
              "batch_id": batch_id, "account_id": record.account_id,
              "account_key": account_key, "control_event": kind,
              "control_mode": None, "strategy_id": None,
              "enabled": None, "policy_hash": None,
              "entries_paused": None, "reason": payload.get("reason")}
    if not isinstance(detail["reason"], str):
        raise ValueError("Portfolio control reason is not scalar text")
    if kind == "control_changed":
        if (set(payload) != lineage | {"event", "control_mode", "reason"}
                or record.entity_id != account_key
                or not isinstance(payload["control_mode"], str)
                or payload["control_mode"] not in {mode.value for mode in PortfolioControlMode}):
            raise ValueError("Portfolio control change is not exact")
        detail["control_mode"] = payload["control_mode"]
    elif kind == "strategy_allocation_control_changed":
        strategy_id = payload.get("strategy_id")
        if (set(payload) != lineage | {"event", "strategy_id", "enabled", "reason"}
                or not isinstance(strategy_id, str) or not strategy_id
                or record.entity_id != f"{account_key}:{strategy_id}"
                or type(payload["enabled"]) is not bool):
            raise ValueError("Portfolio strategy allocation control is not exact")
        detail["strategy_id"] = strategy_id
        detail["enabled"] = int(payload["enabled"])
    elif kind == "portfolio_policy_selected":
        from src.backend.backtest_policy_selection_v3 import project_policy_selection_v3

        selection = project_policy_selection_v3(record, account_key=account_key)
        detail["policy_hash"] = selection.policy_hash
        detail["entries_paused"] = 1
    else:
        raise ValueError("Portfolio control variant lacks a closed scalar contract")
    event = {"record_id": record.record_id, "run_id": record.run_id,
             "event_month": detail["event_month"], "batch_id": batch_id,
             "attempt_id": attempt_id, "sequence": record.sequence,
             "account_id": record.account_id, "event_time": _utc(record.event_time),
             "recorded_at": _utc(record.recorded_at), "category": record.category,
             "entity_type": record.entity_type, "entity_id": record.entity_id,
             "correlation_id": payload["correlation_id"],
             "causation_id": payload["causation_id"]}
    return ControlProjection(event, {**detail,
        "content_hash": sha256(canonical_json(detail).encode("utf-8")).hexdigest()})


def seal_portfolio_control_v3(
    details: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    events: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
    *, run_id: str, batch_id: str,
) -> dict[str, Any]:
    """Validate exact scalar children and return their V3 count/hash seal."""
    batch = str(UUID(batch_id))
    by_id = {str(UUID(str(row["record_id"]))): row for row in events}
    if len(by_id) != len(events):
        raise ValueError("Portfolio control parent event repeats")
    required = {key for key, row in by_id.items() if
                (row["category"], row["entity_type"]) ==
                ("portfolio_management", "portfolio_control")}
    fields = {name for name, _ in CONTROL.columns}
    found: set[str] = set()
    identities: list[tuple[str, str]] = []
    for row in details:
        if set(row) != fields:
            raise ValueError("Portfolio control child columns differ")
        identity = str(UUID(str(row["record_id"])))
        event = by_id.get(identity)
        if identity in found or identity not in required or event is None:
            raise ValueError("Portfolio control child identity differs")
        found.add(identity)
        if (row["run_id"] != run_id or event["run_id"] != run_id
                or str(UUID(str(row["batch_id"]))) != batch
                or str(UUID(str(event["batch_id"]))) != batch
                or row["account_id"] != event["account_id"]
                or row["event_month"] != event["event_month"]
                or not isinstance(row["account_key"], str)
                or not row["account_key"] or not isinstance(row["reason"], str)):
            raise ValueError("Portfolio control child differs from event")
        kind = row["control_event"]
        if kind == "control_changed":
            if (event["entity_id"] != row["account_key"]
                    or row["control_mode"] not in
                    {mode.value for mode in PortfolioControlMode}
                    or row["strategy_id"] is not None or row["enabled"] is not None
                    or row["policy_hash"] is not None
                    or row["entries_paused"] is not None):
                raise ValueError("Portfolio control mode child differs")
        elif kind == "strategy_allocation_control_changed":
            if (not isinstance(row["strategy_id"], str) or not row["strategy_id"]
                    or event["entity_id"] !=
                    f"{row['account_key']}:{row['strategy_id']}"
                    or row["control_mode"] is not None
                    or row["policy_hash"] is not None
                    or row["entries_paused"] is not None
                    or type(row["enabled"]) is not int
                    or row["enabled"] not in (0, 1)):
                raise ValueError("Portfolio allocation control child differs")
        elif kind == "portfolio_policy_selected":
            import re
            if (event["entity_id"] != row["account_key"]
                    or row["control_mode"] is not None
                    or row["strategy_id"] is not None
                    or row["enabled"] is not None
                    or row["entries_paused"] != 1
                    or not isinstance(row["policy_hash"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", row["policy_hash"]) is None):
                raise ValueError("Portfolio policy selection child differs")
        else:
            raise ValueError("Portfolio control variant lacks a closed scalar contract")
        content = {key: value for key, value in row.items() if key != "content_hash"}
        digest = sha256(canonical_json(content).encode("utf-8")).hexdigest()
        if row["content_hash"] != digest:
            raise ValueError("Portfolio control child content hash differs")
        identities.append((identity, digest))
    if found != required:
        raise ValueError("Portfolio control parent lacks exact typed child")
    return {"portfolio_control_count": len(details),
            "portfolio_control_hash": sha256(canonical_json(
                sorted(identities)).encode("utf-8")).hexdigest()}
