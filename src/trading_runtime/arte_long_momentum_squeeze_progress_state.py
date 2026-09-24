"""Inactive typed MACD and target-progression slice for Early Squeeze v24.

Inputs are explicitly selected subfamilies; this is not whole assignment state.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from math import isfinite
from typing import Any

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_IDENTITY = (("run_id", "String"), ("assignment_id", "String"),
             ("state_revision", "UInt64"), ("session", "Date"))
_MACD_KINDS = frozenset({"episode_1s", "gate_100ms", "completed_1s_base", "completed_1s",
                         "completed_5s", "completed_10s", "completed_30s"})
_MACD_FIELDS = {
    "at": "Float64", "observed_at": "Float64", "episode_id": "Float64",
    "line": "Float64", "signal": "Float64", "slow": "Float64", "open": "Bool",
}
_PROGRESS_FIELDS = {
    "session_target_multiplier": "UInt32", "entry_target_multiplier": "UInt32",
    "entry_submitted_multiplier": "UInt32", "entry_target_session_step": "UInt32",
}
MANIFEST_TABLE = TableContract(
    "trading_assignment_squeeze_progress_manifest_v1",
    _IDENTITY + (("macd_row_count", "UInt32"), ("macd_set_hash", "FixedString(64)"),
                 ("session_targets_present", "Bool"), ("entry_present", "Bool"),
                 ("session_broken_present", "Bool"), ("entry_broken_present", "Bool"),
                 ("session_broken_count", "UInt32"), ("entry_broken_count", "UInt32"),
                 ("progress_set_hash", "FixedString(64)"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision",
)
MACD_TABLE = TableContract(
    "trading_assignment_squeeze_macd_state_v1",
    _IDENTITY + (("macd_kind", "LowCardinality(String)"),)
    + tuple((f"{key}_present", "Bool") for key in _MACD_FIELDS)
    + tuple((key, f"Nullable({kind})") for key, kind in _MACD_FIELDS.items())
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision, macd_kind",
)
PROGRESS_TABLE = TableContract(
    "trading_assignment_squeeze_target_progress_v1",
    _IDENTITY + tuple((f"{key}_present", "Bool") for key in _PROGRESS_FIELDS)
    + tuple((key, f"Nullable({kind})") for key, kind in _PROGRESS_FIELDS.items())
    + (("content_hash", "FixedString(64)"),),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision",
)
BROKEN_LEVEL_TABLE = TableContract(
    "trading_assignment_squeeze_progress_broken_level_v1",
    _IDENTITY + (("owner", "LowCardinality(String)"), ("ordinal", "UInt32"),
                 ("unified_level_id", "String"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, state_revision, owner, ordinal",
)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _identity(*, run_id: str, assignment_id: str, state_revision: int,
              session: str) -> dict[str, Any]:
    if (not isinstance(run_id, str) or not run_id or not isinstance(assignment_id, str)
            or not assignment_id or type(state_revision) is not int or state_revision < 0):
        raise ValueError("invalid squeeze progress identity")
    if not isinstance(session, str) or date.fromisoformat(session).isoformat() != session:
        raise ValueError("invalid squeeze progress session")
    return dict(run_id=run_id, assignment_id=assignment_id,
                state_revision=state_revision, session=session)


def _scalar(value: Any, kind: str, key: str) -> Any:
    if value is None:
        return None
    if kind == "Bool":
        if type(value) is not bool:
            raise ValueError(f"{key} must be bool")
        return value
    if kind == "UInt32":
        if type(value) is not int or not 0 <= value < 2**32:
            raise ValueError(f"{key} must be unsigned integer")
        return value
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{key} must be finite numeric")
    return float(value)


def _field_row(values: Mapping[str, Any], fields: dict[str, str],
               identity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) - set(fields):
        raise ValueError("unmodeled squeeze progress field")
    return _seal({**identity,
                  **{f"{key}_present": key in values for key in fields},
                  **{key: _scalar(values[key], kind, key) if key in values else None
                     for key, kind in fields.items()}})


def _keys(values: Any) -> list[str]:
    if (not isinstance(values, list) or len(values) > 4096
            or any(not isinstance(key, str) or not key for key in values)
            or len(values) != len(set(values))):
        raise ValueError("broken levels must be bounded unique string list")
    return values


def project_squeeze_progress_state(
    *, macd: Mapping[str, Mapping[str, Any]],
    session_targets: Mapping[str, Any] | None,
    entry_progress: Mapping[str, Any] | None,
    run_id: str, assignment_id: str, state_revision: int, session: str,
) -> dict[str, Any]:
    """Project selected MACD and target-progression keys; reject extensions."""
    identity = _identity(run_id=run_id, assignment_id=assignment_id,
                         state_revision=state_revision, session=session)
    if not isinstance(macd, Mapping) or set(macd) - _MACD_KINDS:
        raise ValueError("unmodeled MACD state kind")
    macd_rows = []
    for kind in sorted(macd):
        values = macd[kind]
        if kind.startswith("completed_") and set(values) - {"at", "line", "signal", "slow"}:
            raise ValueError("unmodeled completed MACD field")
        if kind == "gate_100ms" and set(values) - {"observed_at", "line", "signal", "open"}:
            raise ValueError("unmodeled MACD gate field")
        if kind == "episode_1s" and set(values) - {"observed_at", "episode_id", "line", "signal", "open"}:
            raise ValueError("unmodeled MACD episode field")
        macd_rows.append(_field_row(values, _MACD_FIELDS, {**identity, "macd_kind": kind}))
    for value, name in ((session_targets, "session_targets"), (entry_progress, "entry_progress")):
        if value is not None and not isinstance(value, Mapping):
            raise ValueError(f"{name} must be mapping or absent")
    session_values = dict(session_targets or {})
    entry_values = dict(entry_progress or {})
    if set(session_values) - {"broken_levels", "target_multiplier"}:
        raise ValueError("unmodeled session target field")
    if set(entry_values) - {"broken_levels", "target_multiplier", "submitted_multiplier",
                             "target_session_step"}:
        raise ValueError("unmodeled entry target field")
    children = []
    for owner, values in (("session", session_values), ("entry", entry_values)):
        for ordinal, key in enumerate(_keys(values.get("broken_levels", []))):
            children.append(_seal({**identity, "owner": owner, "ordinal": ordinal,
                                   "unified_level_id": key}))
    progress_values = {
        **({"session_target_multiplier": session_values["target_multiplier"]}
           if "target_multiplier" in session_values else {}),
        **({"entry_target_multiplier": entry_values["target_multiplier"]}
           if "target_multiplier" in entry_values else {}),
        **({"entry_submitted_multiplier": entry_values["submitted_multiplier"]}
           if "submitted_multiplier" in entry_values else {}),
        **({"entry_target_session_step": entry_values["target_session_step"]}
           if "target_session_step" in entry_values else {}),
    }
    progress = _field_row(progress_values, _PROGRESS_FIELDS, identity)
    manifest = _seal({**identity, "macd_row_count": len(macd_rows),
                      "macd_set_hash": _hash(macd_rows),
                      "session_targets_present": session_targets is not None,
                      "entry_present": entry_progress is not None,
                      "session_broken_present": "broken_levels" in session_values,
                      "entry_broken_present": "broken_levels" in entry_values,
                      "session_broken_count": sum(row["owner"] == "session" for row in children),
                      "entry_broken_count": sum(row["owner"] == "entry" for row in children),
                      "progress_set_hash": _hash({"progress": progress, "levels": children})})
    return dict(manifest=manifest, macd=macd_rows, progress=progress,
                broken_levels=children)


def _validate(row: Any, table: TableContract, identity: dict[str, Any]) -> None:
    if (not isinstance(row, Mapping) or set(row) != {key for key, _ in table.columns}
            or any(row[key] != value for key, value in identity.items())):
        raise ValueError("invalid or mixed squeeze progress row")
    if row != _seal({key: value for key, value in row.items() if key != "content_hash"}):
        raise ValueError("tampered squeeze progress row")


def _read_fields(row: Mapping[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    values = {}
    for key in fields:
        present = row[f"{key}_present"]
        if type(present) is not bool or not present and row[key] is not None:
            raise ValueError("invalid squeeze progress presence flag")
        if present:
            values[key] = row[key]
    return values


def restore_squeeze_progress_state(rows: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(rows, Mapping) or set(rows) != {
        "manifest", "macd", "progress", "broken_levels",
    }:
        raise ValueError("incomplete squeeze progress families")
    manifest = rows["manifest"]
    if not isinstance(manifest, Mapping):
        raise ValueError("invalid squeeze progress manifest")
    identity = {key: manifest[key] for key, _ in _IDENTITY}
    _identity(**identity)
    _validate(manifest, MANIFEST_TABLE, identity)
    macd_rows, children = rows["macd"], rows["broken_levels"]
    if not isinstance(macd_rows, list) or not isinstance(children, list):
        raise ValueError("squeeze progress children must be lists")
    _validate(rows["progress"], PROGRESS_TABLE, identity)
    macd = {}
    for row in macd_rows:
        _validate(row, MACD_TABLE, identity)
        kind = row["macd_kind"]
        if kind in macd:
            raise ValueError("duplicate MACD kind")
        macd[kind] = _read_fields(row, _MACD_FIELDS)
    session, entry = {}, {}
    progress = _read_fields(rows["progress"], _PROGRESS_FIELDS)
    if "session_target_multiplier" in progress:
        session["target_multiplier"] = progress["session_target_multiplier"]
    for source, target in (("entry_target_multiplier", "target_multiplier"),
                           ("entry_submitted_multiplier", "submitted_multiplier"),
                           ("entry_target_session_step", "target_session_step")):
        if source in progress:
            entry[target] = progress[source]
    for owner, values, flag in (("session", session, "session_broken_present"),
                                ("entry", entry, "entry_broken_present")):
        if type(manifest[flag]) is not bool:
            raise ValueError("invalid broken-level presence")
        selected = [row for row in children if row["owner"] == owner]
        for ordinal, row in enumerate(selected):
            _validate(row, BROKEN_LEVEL_TABLE, identity)
            if row["ordinal"] != ordinal:
                raise ValueError("reordered broken levels")
        if manifest[flag]:
            values["broken_levels"] = [row["unified_level_id"] for row in selected]
        elif selected:
            raise ValueError("orphan broken levels")
    restored = dict(macd=macd,
                    session_targets=session if manifest["session_targets_present"] else None,
                    entry_progress=entry if manifest["entry_present"] else None)
    if rows != project_squeeze_progress_state(**restored, **identity):
        raise ValueError("incomplete or noncanonical squeeze progress rows")
    return restored
