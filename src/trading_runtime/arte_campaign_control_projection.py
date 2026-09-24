"""Inactive typed campaign identity, policy and manual-flag state v1.

This is one closed assignment-state family, not a complete cold assignment.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_campaign import SUPPORTED_AUTHORITIES


_IDENTITY = (
    "campaign_id", "campaign_deployment_id", "campaign_profile_id",
    "campaign_book_id", "campaign_universe_id", "campaign_side",
)
_FLAGS = (
    "manual_entry_requested", "force_entry_requested",
    "manual_exit_requested", "disable_after_exit",
)
_POLICY_TEXT = (
    "initial_entry_authority", "reentry_authority", "exit_authority",
    "protective_exit_authority", "session_end_behavior",
)
_POLICY_INTS = (
    "maximum_reentries", "reentry_cooldown_ms", "maximum_initial_watch_ms",
)
_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
TABLES = (
    TableContract("trading_assignment_campaign_control_v1",
                  _KEY + tuple((key, "String") for key in _IDENTITY)
                  + tuple((key, "Nullable(UInt8)") for key in _FLAGS)
                  + (("content_hash", "FixedString(64)"),),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id"),
    TableContract("trading_assignment_campaign_policy_v1",
                  _KEY + tuple((key, "Nullable(String)") for key in _POLICY_TEXT)
                  + tuple((key, "Nullable(UInt64)") for key in _POLICY_INTS)
                  + (("content_hash", "FixedString(64)"),),
                  "toYYYYMM(session)", "assignment_id, revision, snapshot_id"),
)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def _verify(row: dict[str, Any]) -> None:
    content = {key: value for key, value in row.items() if key != "content_hash"}
    if _seal(content)["content_hash"] != row.get("content_hash"):
        raise ValueError("campaign control row content hash mismatch")


def project_campaign_control_state(
    state: Mapping[str, Any], *, assignment_id: str, revision: int,
    snapshot_id: str, session: str,
) -> dict[str, list[dict[str, Any]]]:
    """Project the complete control-family slice, rejecting unmodeled keys."""
    if not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("campaign control identity/revision is invalid")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("campaign control session is invalid")
    if not isinstance(state, Mapping) or set(state) - (set(_IDENTITY) | set(_FLAGS) | {"campaign_policy"}):
        raise ValueError("campaign control state has unmodeled fields")
    if set(_IDENTITY) - set(state):
        raise ValueError("campaign control identity is incomplete")
    for key in _IDENTITY:
        if not isinstance(state[key], str):
            raise ValueError(f"campaign control {key} must be text")
    if not state["campaign_id"] or not state["campaign_book_id"] or state["campaign_side"] not in {"long", "short"}:
        raise ValueError("campaign control identity values are invalid")
    for key in _FLAGS:
        if key in state and type(state[key]) is not bool:
            raise ValueError(f"campaign control {key} must be boolean")
    common = dict(assignment_id=assignment_id, revision=revision,
                  snapshot_id=snapshot_id, session=session)
    control = {**common, **{key: state[key] for key in _IDENTITY},
               **{key: int(state[key]) if key in state else None for key in _FLAGS}}
    policy_rows: list[dict[str, Any]] = []
    if "campaign_policy" in state:
        policy = state["campaign_policy"]
        if not isinstance(policy, Mapping) or set(policy) - (set(_POLICY_TEXT) | set(_POLICY_INTS)):
            raise ValueError("campaign policy has unmodeled fields")
        for key in _POLICY_TEXT:
            if key in policy and not isinstance(policy[key], str):
                raise ValueError(f"campaign policy {key} must be text")
        interactive = {"manual", "confirm", "automatic"}
        domains = {
            "initial_entry_authority": interactive,
            "reentry_authority": SUPPORTED_AUTHORITIES,
            "exit_authority": interactive,
            "protective_exit_authority": {"automatic"},
            "session_end_behavior": {"keep_watching", "stop_when_flat", "exit_and_stop"},
        }
        for key, allowed in domains.items():
            if key in policy and policy[key] not in allowed:
                raise ValueError(f"campaign policy {key} is unsupported")
        for key in _POLICY_INTS:
            if key in policy and (type(policy[key]) is not int or policy[key] < 0):
                raise ValueError(f"campaign policy {key} must be nonnegative integer")
        policy_rows.append(_seal({**common, **{key: policy.get(key) for key in _POLICY_TEXT},
                                  **{key: policy.get(key) for key in _POLICY_INTS}}))
    return {"control": [_seal(control)], "policy": policy_rows}


def restore_campaign_control_state(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Recover the exact state slice from verified typed rows."""
    if set(rows) != {"control", "policy"} or len(rows["control"]) != 1 or len(rows["policy"]) > 1:
        raise ValueError("campaign control typed families are incomplete")
    for family in rows.values():
        for row in family:
            _verify(row)
    control = rows["control"][0]
    common = {key: control[key] for key, _ in _KEY}
    state: dict[str, Any] = {key: control[key] for key in _IDENTITY}
    state.update({key: bool(control[key]) for key in _FLAGS if control[key] is not None})
    if rows["policy"]:
        policy = rows["policy"][0]
        if any(policy[key] != value for key, value in common.items()):
            raise ValueError("campaign policy fence differs")
        state["campaign_policy"] = {
            **{key: policy[key] for key in _POLICY_TEXT if policy[key] is not None},
            **{key: policy[key] for key in _POLICY_INTS if policy[key] is not None},
        }
    if project_campaign_control_state(state, **common) != rows:
        raise ValueError("campaign control readback differs")
    return state
