"""Inactive ordered VWAP entry resistance-ID state, not the full entry."""
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
    "trading_assignment_vwap_entry_ids_v1",
    _KEY + (("broken_present", "Bool"), ("cross_known_present", "Bool"),
            ("broken_count", "UInt16"), ("broken_hash", "FixedString(64)"),
            ("cross_known_count", "UInt16"),
            ("cross_known_hash", "FixedString(64)"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
BROKEN_TABLE = TableContract(
    "trading_assignment_vwap_entry_broken_id_v1",
    _KEY + (("ordinal", "UInt16"), ("level_id", "String"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
CROSS_KNOWN_TABLE = TableContract(
    "trading_assignment_vwap_entry_cross_known_id_v1",
    _KEY + (("ordinal", "UInt16"), ("level_id", "String"),
            ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id, ordinal",
)
TABLES = (MANIFEST_TABLE, BROKEN_TABLE, CROSS_KNOWN_TABLE)
_FIELDS = ("broken", "cross_known")


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def validate_typed_entry_id_lists(value: Mapping[str, Any]) -> dict[str, list[str]]:
    """Validate only the two ID-list fields at opt-in VWAP producer sites."""
    if not isinstance(value, Mapping) or set(value) - set(_FIELDS):
        raise ValueError("VWAP entry IDs have unmodeled fields")
    result = {}
    for name in _FIELDS:
        if name not in value:
            continue
        items = value[name]
        if (type(items) is not list or len(items) > 65535
                or any(type(item) is not str or not item for item in items)
                or len(set(items)) != len(items)):
            raise ValueError(f"VWAP entry {name} must be bounded unique String IDs")
        result[name] = list(items)
    return result


def project_entry_id_lists(value: Mapping[str, Any], *, assignment_id: str,
                           revision: int, snapshot_id: str,
                           session: str) -> dict[str, Any]:
    source = validate_typed_entry_id_lists(value)
    if type(assignment_id) is not str or not assignment_id or type(revision) is not int or revision < 1:
        raise ValueError("VWAP entry IDs assignment identity is invalid")
    try:
        if str(UUID(snapshot_id)) != snapshot_id or date.fromisoformat(session).isoformat() != session:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("VWAP entry IDs snapshot identity is invalid") from exc
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session)
    children = {}
    for name in _FIELDS:
        children[name] = [_seal({**identity, "ordinal": ordinal, "level_id": item})
                          for ordinal, item in enumerate(source.get(name, []))]
    manifest = _seal({**identity,
                      "broken_present": "broken" in source,
                      "cross_known_present": "cross_known" in source,
                      "broken_count": len(children["broken"]),
                      "broken_hash": _hash(children["broken"]),
                      "cross_known_count": len(children["cross_known"]),
                      "cross_known_hash": _hash(children["cross_known"])})
    return {"manifest": manifest, **children}


def restore_entry_id_lists(rows: Mapping[str, Any]) -> dict[str, list[str]]:
    if not isinstance(rows, Mapping) or set(rows) != {"manifest", *_FIELDS}:
        raise ValueError("VWAP entry ID rows are incomplete")
    manifest = rows["manifest"]
    if not isinstance(manifest, Mapping) or set(manifest) != {
            name for name, _ in MANIFEST_TABLE.columns}:
        raise ValueError("VWAP entry ID manifest columns differ")
    source = {}
    for name, table in (("broken", BROKEN_TABLE), ("cross_known", CROSS_KNOWN_TABLE)):
        children = rows[name]
        if (type(children) is not list
                or any(not isinstance(row, Mapping)
                       or set(row) != {key for key, _ in table.columns} for row in children)
                or type(manifest[f"{name}_present"]) is not bool
                or (not manifest[f"{name}_present"] and children)
                or manifest[f"{name}_count"] != len(children)
                or manifest[f"{name}_hash"] != _hash(children)
                or [row["ordinal"] for row in children] != list(range(len(children)))):
            raise ValueError(f"VWAP entry {name} row set differs")
        if manifest[f"{name}_present"]:
            source[name] = [row["level_id"] for row in children]
    expected = project_entry_id_lists(
        source, assignment_id=manifest["assignment_id"],
        revision=manifest["revision"], snapshot_id=manifest["snapshot_id"],
        session=manifest["session"])
    if expected != rows:
        raise ValueError("VWAP entry ID content or identity differs")
    return source
