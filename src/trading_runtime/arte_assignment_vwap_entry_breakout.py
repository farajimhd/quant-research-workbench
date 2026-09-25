"""Inactive VWAP entry breakout-setup evidence, distinct from pending clock rows.

Only ``vwap_ladder_entry.breakout_setup`` is modeled here. The containing entry
state has other unmodeled fields and remains rejected by the composite.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_assignment_pending_breakout import (
    PARENT_TABLE as CLOCK_PENDING_PARENT,
    MEMBER_TABLE as CLOCK_PENDING_MEMBER,
    project_pending_breakout, restore_pending_breakout,
    validate_pending_breakout,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
SETUP_TABLE = TableContract(
    "trading_assignment_vwap_entry_breakout_setup_v1",
    _KEY + (("present", "Bool"), ("child_count", "UInt16"),
            ("child_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
BREAKOUT_PARENT_TABLE = TableContract(
    "trading_assignment_vwap_entry_breakout_parent_v1",
    CLOCK_PENDING_PARENT.columns, CLOCK_PENDING_PARENT.partition,
    CLOCK_PENDING_PARENT.order,
)
BREAKOUT_MEMBER_TABLE = TableContract(
    "trading_assignment_vwap_entry_breakout_member_v1",
    CLOCK_PENDING_MEMBER.columns, CLOCK_PENDING_MEMBER.partition,
    CLOCK_PENDING_MEMBER.order,
)
TABLES = (SETUP_TABLE, BREAKOUT_PARENT_TABLE, BREAKOUT_MEMBER_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def validate_entry_breakout_setup(value: Mapping[str, Any]) -> dict[str, Any]:
    """Typed-mode-only check at the actual VWAP entry producer boundary."""
    return validate_pending_breakout(value)


def project_entry_breakout_setup(value: Any, *, assignment_id: str,
                                 revision: int, snapshot_id: str,
                                 session: str) -> dict[str, Any]:
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP entry breakout identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP entry breakout snapshot is invalid") from exc
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    pending = (project_pending_breakout(validate_entry_breakout_setup(value), **identity)
               if value is not None else None)
    parent = [] if pending is None else [pending["parent"]]
    members = [] if pending is None else pending["members"]
    content = {**identity, "present": pending is not None,
               "child_count": len(parent) + len(members),
               "child_hash": _hash((parent, members))}
    return {"setup": {**content, "content_hash": _hash(content)},
            "parent": parent, "members": members}


def restore_entry_breakout_setup(rows: Mapping[str, Any]) -> dict[str, Any] | None:
    if not isinstance(rows, Mapping) or set(rows) != {"setup", "parent", "members"}:
        raise ValueError("VWAP entry breakout rows are incomplete")
    setup, parent, members = rows["setup"], rows["parent"], rows["members"]
    if (not isinstance(setup, Mapping)
            or set(setup) != {name for name, _ in SETUP_TABLE.columns}
            or type(parent) is not list or type(members) is not list
            or type(setup["present"]) is not bool
            or len(parent) != int(setup["present"])
            or setup["child_count"] != len(parent) + len(members)
            or setup["child_hash"] != _hash((parent, members))):
        raise ValueError("VWAP entry breakout family fence differs")
    value = (restore_pending_breakout({"parent": parent[0], "members": members})
             if setup["present"] else None)
    expected = project_entry_breakout_setup(
        value, assignment_id=setup["assignment_id"], revision=setup["revision"],
        snapshot_id=setup["snapshot_id"], session=setup["session"])
    if expected != rows:
        raise ValueError("VWAP entry breakout content or identity differs")
    return value
