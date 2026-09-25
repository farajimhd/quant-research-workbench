"""Inactive, ordered position-entry level identity and tranche state."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Any, Mapping
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
MANIFEST_TABLE = TableContract(
    "trading_assignment_position_entry_identity_v1",
    _KEY + (("level_ids_present", "Bool"), ("tranches_present", "Bool"),
            ("tranches", "Nullable(UInt16)"), ("level_count", "UInt16"),
            ("level_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
LEVEL_TABLE = TableContract(
    "trading_assignment_position_entry_level_v1",
    _KEY + (("ordinal", "UInt16"), ("level_id", "String"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
TABLES = (MANIFEST_TABLE, LEVEL_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def project_position_entry_identity(state: Mapping[str, Any], *,
                                    assignment_id: str, revision: int,
                                    snapshot_id: str, session: str) -> dict[str, Any]:
    if not isinstance(state, Mapping) or set(state) - {
            "position_entry_level_ids", "position_entry_tranches"}:
        raise ValueError("position-entry identity has unmodeled fields")
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("position-entry assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("position-entry snapshot identity is invalid") from exc
    levels_present = "position_entry_level_ids" in state
    tranches_present = "position_entry_tranches" in state
    levels = state.get("position_entry_level_ids", [])
    tranches = state.get("position_entry_tranches")
    if (type(levels) is not list or len(levels) > 256
            or any(type(level) is not str or not level for level in levels)
            or len(set(levels)) != len(levels)):
        raise ValueError("position-entry levels must be bounded unique IDs")
    if tranches_present and (type(tranches) is not int or not 0 <= tranches <= 65535):
        raise ValueError("position-entry tranche count is invalid")
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    rows = [_seal({**identity, "ordinal": ordinal, "level_id": level})
            for ordinal, level in enumerate(levels)]
    manifest = _seal({**identity, "level_ids_present": levels_present,
                      "tranches_present": tranches_present,
                      "tranches": tranches, "level_count": len(rows),
                      "level_hash": _hash(rows)})
    return {"manifest": manifest, "levels": rows}


def restore_position_entry_identity(rows: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(rows, Mapping) or set(rows) != {"manifest", "levels"}:
        raise ValueError("position-entry rows are incomplete")
    manifest, levels = rows["manifest"], rows["levels"]
    if (not isinstance(manifest, Mapping)
            or set(manifest) != {name for name, _ in MANIFEST_TABLE.columns}
            or type(levels) is not list
            or any(not isinstance(row, Mapping)
                   or set(row) != {name for name, _ in LEVEL_TABLE.columns}
                   for row in levels)):
        raise ValueError("position-entry columns differ")
    if (type(manifest["level_ids_present"]) is not bool
            or type(manifest["tranches_present"]) is not bool
            or (not manifest["level_ids_present"] and levels)
            or (not manifest["tranches_present"] and manifest["tranches"] is not None)
            or manifest["level_count"] != len(levels)
            or manifest["level_hash"] != _hash(levels)
            or [row["ordinal"] for row in levels] != list(range(len(levels)))):
        raise ValueError("position-entry row set differs")
    state: dict[str, Any] = {}
    if manifest["level_ids_present"]:
        state["position_entry_level_ids"] = [row["level_id"] for row in levels]
    if manifest["tranches_present"]:
        state["position_entry_tranches"] = manifest["tranches"]
    expected = project_position_entry_identity(
        state, assignment_id=manifest["assignment_id"],
        revision=manifest["revision"], snapshot_id=manifest["snapshot_id"],
        session=manifest["session"])
    if expected != rows:
        raise ValueError("position-entry content or identity differs")
    return state
