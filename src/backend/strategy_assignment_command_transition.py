"""Inactive, route-compatible strategy-assignment command preparation.

This does not publish or mutate authority. A future fenced typed route must
commit a command against a separately recovered assignment base before using
the returned state. The active SQLite route remains unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.trading_runtime.arte_assignment_command_projection import COMMANDS, STATUS_COMMANDS


def prepare_assignment_command(
    assignment: Mapping[str, Any], command: str, *,
    detail: Mapping[str, Any] | None = None, updated_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the route's saved shape and journal payload without side effects.

    No actor identity is inferred. Nonempty detail has no typed schema and is
    rejected before a future authority write rather than silently discarded.
    """
    command = command.strip().lower()
    if command not in COMMANDS:
        raise ValueError(f"Unsupported strategy assignment command: {command}")
    if detail:
        raise ValueError("assignment command detail is not yet typed")
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise ValueError("updated_at must be timezone-aware")
    for key in ("assignment_id", "account_id", "strategy_id", "strategy_revision", "ticker", "status"):
        if key not in assignment:
            raise ValueError(f"assignment {key} is missing")
    state = assignment.get("state")
    if not isinstance(state, Mapping):
        raise ValueError("assignment state must be a mapping")
    next_state = dict(state)
    if command == "disable_after_exit":
        next_state["disable_after_exit"] = True
    elif command == "request_entry":
        next_state["manual_entry_requested"] = True
    elif command == "force_entry":
        next_state["force_entry_requested"] = True
    elif command in {"request_exit", "exit_and_stop", "exit_keep_watching"}:
        next_state["manual_exit_requested"] = True
        next_state["disable_after_exit"] = command == "exit_and_stop"
    status = STATUS_COMMANDS.get(command, str(assignment["status"]))
    saved = {
        **assignment, "status": status, "state": next_state,
        "updated_at": updated_at.astimezone(timezone.utc).isoformat(),
    }
    payload = {
        "event": "assignment_command", "command": command,
        "assignment_id": assignment["assignment_id"],
        "strategy_id": assignment["strategy_id"],
        "strategy_revision": assignment["strategy_revision"],
        "ticker": assignment["ticker"], "status": status, "detail": {},
    }
    return saved, payload
