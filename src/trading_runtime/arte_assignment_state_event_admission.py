"""Inactive admission check for the legacy assignment-state activity event.

The event is not a state commit: runtime.py writes the assignment separately and
emits this payload only when status changes. A caller must provide an already
pinned typed snapshot identity before this can validate lossless state content.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .arte_assignment_state_composite import (
    _TOP_LEVEL, project_modeled_assignment_state, restore_modeled_assignment_state,
)
from .strategy_engine import AssignmentStatus


PREAPPEND_EMITTER_FIELDS = frozenset({
    "event", "assignment_id", "strategy_id", "strategy_revision",
    "ticker", "status", "state",
})
MODELED_STATE_KEYS = frozenset(_TOP_LEVEL)


def validate_preappend_assignment_state_payload(
    payload: Mapping[str, Any], *, run_id: str, assignment_id: str,
    revision: int, snapshot_id: str, session: str,
    add_step_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Validate runtime.py's payload immediately before ``append_many``.

    This proves only content eligibility against existing typed state families.
    ``TradingJournal.append_many`` subsequently injects causal IDs into the
    journal payload; this interface intentionally rejects that later shape.
    It cannot prove that the event refers to a committed state snapshot.
    """
    if not isinstance(payload, Mapping) or set(payload) != PREAPPEND_EMITTER_FIELDS:
        raise ValueError("assignment-state event fields differ from emitter contract")
    if payload["event"] != "assignment_state_saved":
        raise ValueError("assignment-state event kind differs")
    if (type(payload["assignment_id"]) is not str
            or payload["assignment_id"] != assignment_id
            or type(payload["strategy_id"]) is not str
            or not payload["strategy_id"]
            or type(payload["strategy_revision"]) is not int
            or payload["strategy_revision"] < 1
            or type(payload["ticker"]) is not str
            or not payload["ticker"]
            or type(payload["status"]) is not str
            or payload["status"] not in {status.value for status in AssignmentStatus}):
        raise ValueError("assignment-state event identity or status is invalid")
    state = payload["state"]
    if not isinstance(state, Mapping):
        raise ValueError("assignment-state event state must be a mapping")
    unknown = set(state) - MODELED_STATE_KEYS
    if unknown:
        raise ValueError(f"assignment-state event has unmodeled state keys: {sorted(unknown)}")
    projected = project_modeled_assignment_state(
        state, run_id=run_id, assignment_id=assignment_id,
        revision=revision, snapshot_id=snapshot_id, session=session,
        add_step_ids=add_step_ids,
    )
    restored = restore_modeled_assignment_state(
        projected, run_id=run_id, assignment_id=assignment_id,
        revision=revision, snapshot_id=snapshot_id, session=session,
        add_step_ids=add_step_ids,
    )
    if restored != state:
        raise ValueError("assignment-state event cannot round-trip")
    return {**{key: payload[key] for key in PREAPPEND_EMITTER_FIELDS if key != "state"},
            "state": restored}
