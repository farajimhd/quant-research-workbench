"""Inactive typed legacy add-step use counts bound to pinned parameter IDs.

Under the closed 14-family typed parameter contract, revisions 26-36 can
only emit the synthetic legacy-confirmed-add step; revisions 37+ cannot emit
this family. Arbitrary historical phase_policy step maps remain inadmissible.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Any, Mapping, Sequence
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_long_momentum_parameters import PARAMETER_FAMILIES
from src.trading_runtime.journal_contract import canonical_json


_KEY = (("assignment_id", "String"), ("revision", "UInt64"),
        ("snapshot_id", "UUID"), ("session", "Date"))
MANIFEST_TABLE = TableContract(
    "trading_assignment_add_step_uses_manifest_v1",
    _KEY + (("present", "Bool"), ("row_count", "UInt32"),
            ("row_set_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
STEP_TABLE = TableContract(
    "trading_assignment_add_step_use_v1",
    _KEY + (("step_id", "String"), ("uses", "UInt64"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, step_id",
)
TABLES = (MANIFEST_TABLE, STEP_TABLE)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def add_step_catalog(parameters: Mapping[str, Any], *, strategy_revision: int) -> tuple[str, ...]:
    """Derive stable row identities from the immutable typed parameter snapshot."""
    if (not isinstance(parameters, Mapping) or set(parameters) != PARAMETER_FAMILIES
            or type(strategy_revision) is not int or not 26 <= strategy_revision <= 47):
        raise ValueError("add-step catalog requires closed pinned Long Momentum parameters")
    add = parameters.get("add")
    if not isinstance(add, Mapping) or type(add.get("enabled")) is not bool:
        raise ValueError("add-step catalog has invalid add parameters")
    return ("legacy-confirmed-add",) if strategy_revision < 37 and add["enabled"] else ()


def _identity(assignment_id: str, revision: int, snapshot_id: str,
              session: str) -> dict[str, Any]:
    if (type(assignment_id) is not str or not assignment_id
            or type(revision) is not int or revision < 1):
        raise ValueError("add-step use identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("add-step use snapshot is invalid") from exc
    return dict(assignment_id=assignment_id, revision=revision,
                snapshot_id=snapshot_id, session=session)


def project_add_step_uses(values: Mapping[str, Any] | None, *, present: bool,
                          allowed_step_ids: Sequence[str], assignment_id: str,
                          revision: int, snapshot_id: str,
                          session: str) -> dict[str, Any]:
    identity = _identity(assignment_id, revision, snapshot_id, session)
    if type(present) is not bool or (not present and values is not None):
        raise ValueError("add-step use presence differs")
    if present and not isinstance(values, Mapping):
        raise ValueError("add-step uses must be a mapping")
    if (not isinstance(allowed_step_ids, (tuple, list))
            or any(type(step) is not str or not step for step in allowed_step_ids)
            or len(set(allowed_step_ids)) != len(allowed_step_ids)):
        raise ValueError("add-step catalog is invalid")
    source = {} if values is None else values
    if set(source) - set(allowed_step_ids):
        raise ValueError("add-step uses contain an uncataloged step")
    for value in source.values():
        if type(value) is not int or value < 0 or value >= 2 ** 64:
            raise ValueError("add-step uses must be UInt64")
    rows = [_seal({**identity, "step_id": key, "uses": source[key]})
            for key in sorted(source)]
    manifest = _seal({**identity, "present": present,
                      "row_count": len(rows), "row_set_hash": _hash(rows)})
    return {"manifest": manifest, "steps": rows}


def restore_add_step_uses(rows: Mapping[str, Any], *,
                          allowed_step_ids: Sequence[str]) -> tuple[bool, dict[str, int]]:
    if not isinstance(rows, Mapping) or set(rows) != {"manifest", "steps"}:
        raise ValueError("add-step use rows are incomplete")
    manifest, steps = rows["manifest"], rows["steps"]
    if (not isinstance(manifest, Mapping) or
            set(manifest) != {name for name, _ in MANIFEST_TABLE.columns}
            or not isinstance(steps, list)):
        raise ValueError("add-step use rows are malformed")
    if any(not isinstance(row, Mapping) or set(row) != {
            name for name, _ in STEP_TABLE.columns} for row in steps):
        raise ValueError("add-step use step columns differ")
    if (type(manifest["present"]) is not bool or
            (not manifest["present"] and steps) or
            manifest["row_count"] != len(steps) or
            manifest["row_set_hash"] != _hash(steps)):
        raise ValueError("add-step use row fence differs")
    if [row["step_id"] for row in steps] != sorted(set(row["step_id"] for row in steps)):
        raise ValueError("add-step use keys are duplicate or unordered")
    values = {row["step_id"]: row["uses"] for row in steps}
    expected = project_add_step_uses(
        values if manifest["present"] else None,
        present=manifest["present"], allowed_step_ids=allowed_step_ids,
        assignment_id=manifest["assignment_id"], revision=manifest["revision"],
        snapshot_id=manifest["snapshot_id"], session=manifest["session"])
    if expected != rows:
        raise ValueError("add-step use exact content differs")
    return manifest["present"], values
