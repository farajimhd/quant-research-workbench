"""Inactive, route-shaped typed projection for strategy assignment commands.

The current command route saves SQLite state and then appends a journal record.
This module projects that *existing record* and saved assignment; it does not
claim to provide an atomic typed replacement for that two-step route.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_writer import (
    TypedJournalBatch, _canonical_typed_content, typed_row,
)
from src.trading_runtime.journal_contract import JournalRecord, canonical_json


STATUS_COMMANDS = {
    "arm": "watching", "resume": "watching", "pause": "paused",
    "disable": "disabled", "complete": "completed",
}
FLAG_COMMANDS = {
    "disable_after_exit", "request_entry", "force_entry", "request_exit",
    "exit_and_stop", "exit_keep_watching",
}
COMMANDS = frozenset(STATUS_COMMANDS) | FLAG_COMMANDS
STATUSES = frozenset({
    "disabled", "watching", "entry_pending", "exit_pending", "managing",
    "reentry_cooldown", "paused", "completed", "error",
})
_PAYLOAD_KEYS = frozenset({
    "event", "command", "assignment_id", "strategy_id", "strategy_revision",
    "ticker", "status", "detail", "correlation_id", "causation_id",
})
_DETAIL_COLUMNS = (
    "record_id", "run_id", "event_month", "batch_id", "account_id",
    "assignment_id", "strategy_id", "strategy_revision", "ticker", "command",
    "status", "updated_at", "source_event_time",
)


def _utc(value: Any, label: str, *, stored_utc: bool = False) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stored_utc and isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _effects(command: str) -> dict[str, int | None]:
    return {
        "manual_entry_set": 1 if command == "request_entry" else None,
        "force_entry_set": 1 if command == "force_entry" else None,
        "manual_exit_set": 1 if command in {
            "request_exit", "exit_and_stop", "exit_keep_watching",
        } else None,
        "disable_after_exit_set": (
            1 if command in {"disable_after_exit", "exit_and_stop"} else
            0 if command in {"request_exit", "exit_keep_watching"} else None
        ),
    }


def project_assignment_command(
    record: JournalRecord, saved_assignment: Mapping[str, Any], *, batch_id: str,
) -> dict[str, Any]:
    """Use journal record_id/sequence as command identity/version; no actor is inferred.

    Repository callers currently send no detail fields. The public API still
    accepts arbitrary detail, so nonempty detail must fail closed until a
    versioned typed detail contract and explicit API policy exist.
    """
    if (record.category, record.entity_type) != ("strategy", "strategy_assignment_command"):
        raise ValueError("assignment command journal category/entity is invalid")
    try:
        UUID(record.record_id)
        UUID(batch_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("assignment command needs UUID record and batch IDs") from exc
    if record.sequence <= 0 or record.run_id != record.entity_id:
        raise ValueError("assignment command has no authoritative run sequence")
    payload = dict(record.payload)
    if (set(payload) - _PAYLOAD_KEYS
            or not (_PAYLOAD_KEYS - {"correlation_id", "causation_id"}) <= set(payload)):
        raise ValueError("assignment command payload has missing or unmodeled fields")
    if payload["event"] != "assignment_command" or payload["command"] not in COMMANDS:
        raise ValueError("assignment command event or variant is invalid")
    if payload["detail"] != {}:
        raise ValueError("assignment command detail is not yet typed; nonempty detail is unsupported")
    command = payload["command"]
    for payload_key, saved_key in (
        ("assignment_id", "assignment_id"), ("strategy_id", "strategy_id"),
        ("strategy_revision", "strategy_revision"), ("ticker", "ticker"),
        ("status", "status"),
    ):
        if payload[payload_key] != saved_assignment.get(saved_key):
            raise ValueError(f"assignment command {payload_key} differs from saved state")
    if (payload["assignment_id"] != record.run_id
            or payload["assignment_id"] != record.entity_id
            or str(saved_assignment.get("account_id") or "") != record.account_id):
        raise ValueError("assignment command identity differs from its journal envelope")
    if type(payload["strategy_revision"]) is not int or payload["strategy_revision"] <= 0:
        raise ValueError("assignment command strategy revision is invalid")
    if command in STATUS_COMMANDS and payload["status"] != STATUS_COMMANDS[command]:
        raise ValueError("assignment command status differs from its command")
    if payload["status"] not in STATUSES:
        raise ValueError("assignment command status is invalid")
    effects = _effects(command)
    state = saved_assignment.get("state")
    if not isinstance(state, Mapping):
        raise ValueError("assignment command saved state is invalid")
    for column, expected in effects.items():
        if expected is not None:
            state_key = column.removesuffix("_set") + "_requested" if column != "disable_after_exit_set" else "disable_after_exit"
            if bool(state.get(state_key)) != bool(expected):
                raise ValueError(f"assignment command saved {state_key} differs from its effect")
    at = _utc(record.event_time, "event_time")
    _utc(record.recorded_at, "recorded_at")
    updated = _utc(saved_assignment.get("updated_at"), "updated_at")
    if not isinstance(saved_assignment.get("assignment_id"), str) or not record.account_id:
        raise ValueError("assignment command saved identity is incomplete")
    row = {
        "record_id": record.record_id, "run_id": record.run_id,
        "event_month": date(at.year, at.month, 1).isoformat(),
        "batch_id": batch_id, "account_id": record.account_id,
        "assignment_id": payload["assignment_id"],
        "strategy_id": payload["strategy_id"],
        "strategy_revision": payload["strategy_revision"],
        "ticker": payload["ticker"], "command": command,
        "status": payload["status"], "updated_at": updated.isoformat(),
        "source_event_time": at.isoformat(),
    }
    if tuple(row) != _DETAIL_COLUMNS:
        raise AssertionError("assignment command typed column contract drifted")
    return typed_row("trading_strategy_assignment_command_v1", row)


def assignment_command_batch(
    record: JournalRecord, saved_assignment: Mapping[str, Any], *,
    run_month: date, attempt_id: str, batch_id: str,
    prior_batch_id: str, source_cursor: str,
) -> TypedJournalBatch:
    """Build one sealed-family-ready batch without changing the live route."""
    detail = project_assignment_command(record, saved_assignment, batch_id=batch_id)
    payload = record.payload
    event = {
        "run_id": record.run_id, "event_month": detail["event_month"],
        "attempt_id": attempt_id, "batch_id": batch_id,
        "record_id": record.record_id, "sequence": record.sequence,
        "event_time": _utc(record.event_time, "event_time").isoformat(),
        "recorded_at": _utc(record.recorded_at, "recorded_at").isoformat(),
        "category": "strategy", "entity_type": "strategy_assignment_command",
        "entity_id": record.entity_id, "account_id": record.account_id,
        "correlation_id": str(payload.get("correlation_id") or ""),
        "causation_id": str(payload.get("causation_id") or ""),
    }
    return TypedJournalBatch(
        record.run_id, run_month, attempt_id, batch_id, prior_batch_id,
        record.sequence, record.sequence, source_cursor, "running", (event,),
        assignment_commands=(detail,),
    )


def recover_assignment_command(
    prior_assignment: Mapping[str, Any], detail: Mapping[str, Any], *,
    record_id: str, sequence: int, stored_utc: bool = False,
) -> dict[str, Any]:
    """Replay one verified committed detail against a separately recovered base."""
    row = dict(detail)
    digest = row.pop("content_hash", None)
    canonical = _canonical_typed_content(
        "trading_strategy_assignment_command_v1", row, stored_utc=stored_utc,
    )
    if digest != sha256(canonical_json(canonical).encode("utf-8")).hexdigest():
        raise ValueError("assignment command detail hash mismatch")
    if tuple(row) != _DETAIL_COLUMNS or row["record_id"] != record_id or sequence <= 0:
        raise ValueError("assignment command detail identity/columns are invalid")
    if row["command"] not in COMMANDS:
        raise ValueError("assignment command detail variant is invalid")
    for key in ("assignment_id", "account_id", "strategy_id", "strategy_revision", "ticker"):
        if row[key] != prior_assignment.get(key):
            raise ValueError(f"assignment command recovery {key} differs from base")
    if row["command"] in STATUS_COMMANDS and row["status"] != STATUS_COMMANDS[row["command"]]:
        raise ValueError("assignment command recovered status is invalid")
    expected = _effects(row["command"])
    state = dict(prior_assignment.get("state") or {})
    for column, value in expected.items():
        if value is not None:
            state_key = column.removesuffix("_set") + "_requested" if column != "disable_after_exit_set" else "disable_after_exit"
            state[state_key] = bool(value)
    updated = _utc(row["updated_at"], "updated_at", stored_utc=stored_utc)
    return {**prior_assignment, "status": row["status"], "state": state,
            "updated_at": updated.isoformat()}
