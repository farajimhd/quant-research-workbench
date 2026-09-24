"""Inactive typed leaf contracts for squeeze V7 geometry and causal clocks.

Accepted level keys are exactly V.levels()/early_squeeze_fast.levels()
output. Accepted pivot keys are StructuralDetector.local_evidence()
without fresh_pivot. Unknown producer extensions fail closed in typed mode.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json


_LEVEL = {
    "unified_level_id": "String", "lower": "Float64", "upper": "Float64",
    "confirmed_at_ms": "Float64", "book_version": "String",
    "price": "Float64", "role": "String", "input_policy": "String",
    "seed_input_policy": "String", "transition_from": "String",
}
_PIVOT = {
    "unified_level_id": "String", "level_id": "String", "lower": "Float64",
    "upper": "Float64", "price": "Float64", "pivot_at": "Float64",
    "confirmed_at": "Float64", "confirmed_at_ms": "Float64",
    "book_version": "String", "scale": "String", "prominence": "Float64",
    "score": "Float64", "selection_score": "Float64",
    "selection_minimum_score": "Float64", "reversal_distance": "Float64",
}
_CLOCK = {"as_of": "Float64", "max_input_timestamp": "Float64"}
_SPECS = {"level": _LEVEL, "pivot": _PIVOT, "structure_clock": _CLOCK}
_IDENTITY = (("run_id", "String"), ("assignment_id", "String"),
             ("state_revision", "UInt64"), ("snapshot_session", "Date"))
SET_TABLE = TableContract(
    "trading_assignment_squeeze_v7_evidence_set_v1",
    _IDENTITY + (("source_path", "String"), ("evidence_family", "LowCardinality(String)"),
                 ("row_count", "UInt32"), ("row_set_hash", "FixedString(64)"),
                 ("content_hash", "FixedString(64)")),
    "toYYYYMM(snapshot_session)",
    "run_id, assignment_id, state_revision, source_path, evidence_family",
)
TABLES = {
    family: TableContract(
        f"trading_assignment_squeeze_v7_{family}_v1",
        _IDENTITY + (("source_path", "String"), ("ordinal", "UInt32"))
        + tuple((f"{key}_present", "Bool") for key in spec)
        + tuple((key, f"Nullable({kind})") for key, kind in spec.items())
        + (("side_integer_present", "Bool"), ("side_integer", "Nullable(Int8)"),
           ("side_label_present", "Bool"), ("side_label", "Nullable(String)"))
        + (("content_hash", "FixedString(64)"),),
        "toYYYYMM(snapshot_session)",
        "run_id, assignment_id, state_revision, source_path, ordinal",
    ) if family != "structure_clock" else TableContract(
        "trading_assignment_squeeze_v7_structure_clock_v1",
        _IDENTITY + (("source_path", "String"), ("ordinal", "UInt32"))
        + tuple((f"{key}_present", "Bool") for key in spec)
        + tuple((key, f"Nullable({kind})") for key, kind in spec.items())
        + (("content_hash", "FixedString(64)"),),
        "toYYYYMM(snapshot_session)",
        "run_id, assignment_id, state_revision, source_path, ordinal",
    ) for family, spec in _SPECS.items()
}


def _scalar(value: Any, kind: str, key: str) -> Any:
    if value is None:
        return None
    if kind == "String":
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be nonempty string")
        return value
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{key} must be finite numeric")
    return float(value)


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": sha256(canonical_json(row).encode()).hexdigest()}


def _identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    from datetime import date
    from src.trading_runtime.arte_long_momentum_squeeze_clock_state import _identity as check
    return check(run_id=identity["run_id"], assignment_id=identity["assignment_id"],
                 state_revision=identity["state_revision"],
                 snapshot_session=identity["snapshot_session"])


def project_v7_evidence(family: str, evidence: Mapping[str, Any], *,
                        source_path: str, ordinal: int, **identity: Any) -> dict[str, Any]:
    """Validate one leaf and project to a fully named scalar row."""
    if family not in _SPECS or not isinstance(evidence, Mapping):
        raise ValueError("unsupported V7 evidence family")
    if not isinstance(source_path, str) or not source_path or type(ordinal) is not int or ordinal < 0:
        raise ValueError("invalid V7 evidence path/ordinal")
    common = _identity(identity)
    spec = _SPECS[family]
    permitted = set(spec) | ({"side"} if family != "structure_clock" else set())
    if set(evidence) - permitted:
        raise ValueError("unmodeled V7 evidence field")
    required = ({"unified_level_id", "lower", "upper", "confirmed_at_ms", "book_version"}
                if family == "level" else
                {"price", "pivot_at", "confirmed_at", "side"} if family == "pivot" else
                {"as_of", "max_input_timestamp"})
    if not required <= set(evidence):
        raise ValueError("missing V7 evidence field")
    side = evidence.get("side")
    if "side" in evidence and side is None:
        raise ValueError("V7 side cannot be null")
    if side is not None and type(side) is not int and (not isinstance(side, str) or not side):
        raise ValueError("invalid V7 side")
    if type(side) is int and side not in (-1, 1):
        raise ValueError("invalid V7 side sign")
    row = {**common, "source_path": source_path, "ordinal": ordinal,
           **{f"{key}_present": key in evidence for key in spec},
           **{key: _scalar(evidence[key], kind, key) if key in evidence else None
              for key, kind in spec.items()}}
    if family != "structure_clock":
        row.update(side_integer_present=type(side) is int,
                   side_integer=side if type(side) is int else None,
                   side_label_present=isinstance(side, str),
                   side_label=side if isinstance(side, str) else None)
    if family == "level":
        if row["book_version"] != "causal-level-book-v7-mle-1" or not 0 < row["lower"] <= row["upper"]:
            raise ValueError("invalid V7 level geometry/version")
    elif family == "pivot":
        if not 0 < row["price"] or not 0 < row["pivot_at"] < row["confirmed_at"]:
            raise ValueError("invalid causal pivot")
    elif not 0 < row["max_input_timestamp"] <= row["as_of"]:
        raise ValueError("invalid V7 structure clock")
    return _seal(row)


def restore_v7_evidence(family: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Reject missing, extra, mixed-type and tampered leaf columns."""
    if family not in TABLES or not isinstance(row, Mapping) or set(row) != {
        key for key, _ in TABLES[family].columns
    }:
        raise ValueError("invalid V7 evidence columns")
    spec = _SPECS[family]
    evidence = {}
    for key in spec:
        present = row[f"{key}_present"]
        if type(present) is not bool or not present and row[key] is not None:
            raise ValueError("invalid V7 field presence")
        if present:
            evidence[key] = row[key]
    if family != "structure_clock":
        integer, label = row["side_integer_present"], row["side_label_present"]
        if type(integer) is not bool or type(label) is not bool or integer and label:
            raise ValueError("invalid V7 side discriminator")
        if integer:
            evidence["side"] = row["side_integer"]
        elif label:
            evidence["side"] = row["side_label"]
        elif row["side_integer"] is not None or row["side_label"] is not None:
            raise ValueError("invalid V7 side absence")
    identity = {key: row[key] for key, _ in _IDENTITY}
    if row != project_v7_evidence(family, evidence, source_path=row["source_path"],
                                  ordinal=row["ordinal"], **identity):
        raise ValueError("V7 evidence content hash mismatch")
    return evidence


def project_v7_evidence_set(family: str, evidence: list[Mapping[str, Any]], *,
                            source_path: str, **identity: Any) -> dict[str, Any]:
    """Seal a bounded complete ordered child set, including an empty set."""
    if not isinstance(evidence, list) or len(evidence) > 4096:
        raise ValueError("V7 evidence set must be bounded list")
    children = [project_v7_evidence(family, item, source_path=source_path,
                                    ordinal=ordinal, **identity)
                for ordinal, item in enumerate(evidence)]
    common = _identity(identity)
    parent = _seal({**common, "source_path": source_path,
                    "evidence_family": family, "row_count": len(children),
                    "row_set_hash": sha256(canonical_json(children).encode()).hexdigest()})
    return {"set": parent, "rows": children}


def restore_v7_evidence_set(projected: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(projected, Mapping) or set(projected) != {"set", "rows"}:
        raise ValueError("V7 evidence set rows incomplete")
    parent, children = projected["set"], projected["rows"]
    if (not isinstance(parent, Mapping) or set(parent) != {key for key, _ in SET_TABLE.columns}
            or not isinstance(children, list)):
        raise ValueError("invalid V7 evidence set columns")
    identity = {key: parent[key] for key, _ in _IDENTITY}
    family = parent["evidence_family"]
    if family not in _SPECS:
        raise ValueError("unknown V7 evidence family")
    evidence = []
    for ordinal, row in enumerate(children):
        if (row["ordinal"] != ordinal or row["source_path"] != parent["source_path"]
                or any(row[key] != value for key, value in identity.items())):
            raise ValueError("mixed or reordered V7 evidence children")
        evidence.append(restore_v7_evidence(family, row))
    if projected != project_v7_evidence_set(
        family, evidence, source_path=parent["source_path"], **identity,
    ):
        raise ValueError("incomplete or tampered V7 evidence set")
    return evidence
