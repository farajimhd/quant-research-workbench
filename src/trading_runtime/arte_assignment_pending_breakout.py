"""Inactive named-column pending VWAP breakout evidence with ordered zone members."""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
_ANCHOR_BASE = ("unified_level_id", "lower", "upper", "price", "side", "role",
                "confirmed_at_ms", "book_version", "input_policy", "seed_input_policy")
_ANCHOR_GROUP = ("members", "encountered", "seen_below", "grouping_threshold", "broken_at")
_OPTIONAL = ("price", "side", "role", "input_policy", "seed_input_policy", "broken_at")
PARENT_TABLE = TableContract(
    "trading_assignment_pending_breakout_v1",
    _KEY + (("witnessed_at", "DateTime64(6, 'UTC')"),
            ("trigger_price", "Float64"), ("target_price", "Float64"),
            ("episode_id", "Float64"), ("anchor_kind", "LowCardinality(String)"),
            ("anchor_presence", "UInt16"), ("anchor_id", "String"),
            ("anchor_lower", "Float64"), ("anchor_upper", "Float64"),
            ("anchor_price", "Nullable(Float64)"), ("anchor_side", "Nullable(Int8)"),
            ("anchor_role", "Nullable(String)"),
            ("anchor_confirmed_at_ms", "Float64"), ("anchor_book_version", "String"),
            ("anchor_input_policy", "Nullable(String)"),
            ("anchor_seed_input_policy", "Nullable(String)"),
            ("anchor_encountered", "Nullable(Bool)"),
            ("anchor_seen_below", "Nullable(Bool)"),
            ("anchor_grouping_threshold", "Nullable(Float64)"),
            ("anchor_broken_at", "Nullable(Float64)"),
            ("member_count", "UInt16"), ("member_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
MEMBER_TABLE = TableContract(
    "trading_assignment_pending_breakout_member_v1",
    _KEY + (("ordinal", "UInt16"), ("member_id", "String"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
TABLES = (PARENT_TABLE, MEMBER_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _number(value: Any, field: str) -> float:
    if type(value) not in (int, float) or not isfinite(value) or abs(value) > 2 ** 53:
        raise ValueError(f"pending breakout {field} is not exact finite Float64")
    return float(value)


def _clock(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("pending breakout witness time is invalid")
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("pending breakout witness time is invalid") from exc
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("pending breakout witness time must be UTC")
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds")


def validate_pending_breakout(pending: Mapping[str, Any]) -> dict[str, Any]:
    """Typed-mode-only source check; legacy producer remains unchanged."""
    from .post_move_entries import validate_typed_breakout_anchor
    if not isinstance(pending, Mapping) or set(pending) != {
            "anchor", "trigger", "witnessed_at", "target_price", "episode_id"}:
        raise ValueError("pending breakout has missing or unmodeled fields")
    anchor = validate_typed_breakout_anchor(pending["anchor"])
    grouped = "members" in anchor
    if grouped and (set(anchor) - set(_ANCHOR_BASE) - set(_ANCHOR_GROUP)):
        raise ValueError("pending breakout grouped anchor has unmodeled fields")
    normalized = dict(anchor)
    for key in ("lower", "upper", "price", "confirmed_at_ms",
                "grouping_threshold", "broken_at"):
        if key in normalized:
            normalized[key] = _number(normalized[key], f"anchor {key}")
    trigger = _number(pending["trigger"], "trigger")
    target = _number(pending["target_price"], "target")
    episode = _number(pending["episode_id"], "episode_id")
    if not 0 < normalized["upper"] < target or not 0 < trigger < target:
        raise ValueError("pending breakout target or trigger geometry is invalid")
    return dict(anchor=normalized, trigger=trigger, witnessed_at=_clock(pending["witnessed_at"]),
                target_price=target, episode_id=episode)


def project_pending_breakout(pending: Mapping[str, Any], *, assignment_id: str,
                             revision: int, snapshot_id: str,
                             session: str) -> dict[str, Any]:
    normalized = validate_pending_breakout(pending)
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("pending breakout assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("pending breakout snapshot identity is invalid") from exc
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    anchor = normalized["anchor"]
    grouped = "members" in anchor
    members = anchor.get("members", [])
    if len(members) > 65535:
        raise ValueError("pending breakout has too many zone members")
    member_rows = [_seal({**identity, "ordinal": ordinal, "member_id": member})
                   for ordinal, member in enumerate(members)]
    mask = sum(1 << ordinal for ordinal, key in enumerate(_OPTIONAL) if key in anchor)
    parent = _seal({**identity, "witnessed_at": normalized["witnessed_at"],
                    "trigger_price": normalized["trigger"],
                    "target_price": normalized["target_price"],
                    "episode_id": normalized["episode_id"],
                    "anchor_kind": "grouped" if grouped else "passive",
                    "anchor_presence": mask,
                    "anchor_id": anchor["unified_level_id"],
                    "anchor_lower": anchor["lower"], "anchor_upper": anchor["upper"],
                    "anchor_price": anchor.get("price"), "anchor_side": anchor.get("side"),
                    "anchor_role": anchor.get("role"),
                    "anchor_confirmed_at_ms": anchor["confirmed_at_ms"],
                    "anchor_book_version": anchor["book_version"],
                    "anchor_input_policy": anchor.get("input_policy"),
                    "anchor_seed_input_policy": anchor.get("seed_input_policy"),
                    "anchor_encountered": anchor.get("encountered"),
                    "anchor_seen_below": anchor.get("seen_below"),
                    "anchor_grouping_threshold": anchor.get("grouping_threshold"),
                    "anchor_broken_at": anchor.get("broken_at"),
                    "member_count": len(member_rows), "member_hash": _hash(member_rows)})
    return {"parent": parent, "members": member_rows}


def restore_pending_breakout(rows: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(rows, Mapping) or set(rows) != {"parent", "members"}:
        raise ValueError("pending breakout rows are incomplete")
    parent, members = rows["parent"], rows["members"]
    if (not isinstance(parent, Mapping)
            or set(parent) != {name for name, _ in PARENT_TABLE.columns}
            or type(members) is not list
            or any(not isinstance(row, Mapping)
                   or set(row) != {name for name, _ in MEMBER_TABLE.columns}
                   for row in members)):
        raise ValueError("pending breakout columns differ")
    if (parent["member_count"] != len(members)
            or parent["member_hash"] != _hash(members)
            or [row["ordinal"] for row in members] != list(range(len(members)))
            or parent["anchor_kind"] not in ("passive", "grouped")):
        raise ValueError("pending breakout member fence differs")
    anchor = dict(unified_level_id=parent["anchor_id"], lower=parent["anchor_lower"],
                  upper=parent["anchor_upper"],
                  confirmed_at_ms=parent["anchor_confirmed_at_ms"],
                  book_version=parent["anchor_book_version"])
    optional_columns = ("anchor_price", "anchor_side", "anchor_role",
                        "anchor_input_policy", "anchor_seed_input_policy", "anchor_broken_at")
    for ordinal, (key, column) in enumerate(zip(_OPTIONAL, optional_columns)):
        value = parent[column]
        if parent["anchor_presence"] & (1 << ordinal):
            anchor[key] = value
        elif value is not None:
            raise ValueError("pending breakout anchor presence differs")
    if parent["anchor_kind"] == "grouped":
        anchor.update(members=[row["member_id"] for row in members],
                      encountered=parent["anchor_encountered"],
                      seen_below=parent["anchor_seen_below"],
                      grouping_threshold=parent["anchor_grouping_threshold"])
    elif members or any(parent[key] is not None for key in (
            "anchor_encountered", "anchor_seen_below", "anchor_grouping_threshold")):
        raise ValueError("passive breakout anchor has grouped fields")
    pending = dict(anchor=anchor, trigger=parent["trigger_price"],
                   witnessed_at=parent["witnessed_at"],
                   target_price=parent["target_price"], episode_id=parent["episode_id"])
    expected = project_pending_breakout(
        pending, assignment_id=parent["assignment_id"], revision=parent["revision"],
        snapshot_id=parent["snapshot_id"], session=parent["session"])
    if expected != rows:
        raise ValueError("pending breakout content or identity differs")
    return pending
