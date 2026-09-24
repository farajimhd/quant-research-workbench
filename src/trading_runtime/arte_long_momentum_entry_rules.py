"""Inactive normalized ordered entry-rule stages, groups and conditions."""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import UUID

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_engine import HISTORICAL_STRATEGY_REVISIONS, STRATEGY_ID, STRATEGY_REVISION


_REVISIONS = frozenset((*HISTORICAL_STRATEGY_REVISIONS, STRATEGY_REVISION))
_STAGES = ("trigger", "confirmation", "veto")
_GROUP = frozenset({"group_id", "label", "operator", "required_score", "enabled", "conditions"})
_CONDITION = frozenset({"condition_id", "comparator", "enabled", "left_source_id",
                        "left_timeframe", "right_source_id", "right_timeframe", "value"})
_KEY = (("assignment_id", "String"), ("strategy_id", "String"),
        ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"), ("session", "Date"))
TABLES = (
    TableContract("trading_assignment_long_momentum_rule_stage_v1",
                  _KEY + (("stage", "LowCardinality(String)"), ("operator", "String"),
                          ("group_count", "UInt32"), ("group_hash", "FixedString(64)"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id, stage"),
    TableContract("trading_assignment_long_momentum_rule_group_v1",
                  _KEY + (("stage", "LowCardinality(String)"), ("ordinal", "UInt32"),
                          ("group_id", "String"), ("label", "String"),
                          ("operator", "String"), ("required_score", "Float64"),
                          ("enabled", "UInt8"), ("condition_count", "UInt32"),
                          ("condition_hash", "FixedString(64)"),
                          ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id, stage, ordinal"),
    TableContract("trading_assignment_long_momentum_rule_condition_v1",
                  _KEY + (("stage", "LowCardinality(String)"), ("group_ordinal", "UInt32"),
                          ("ordinal", "UInt32"), ("condition_id", "String"),
                          ("comparator", "String"), ("enabled", "UInt8"),
                          ("left_source_id", "String"), ("left_timeframe", "String"),
                          ("right_source_id", "String"), ("right_timeframe", "String"),
                          ("value", "Nullable(Float64)"), ("content_hash", "FixedString(64)")),
                  "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id, stage, group_ordinal, ordinal"),
)


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _digest(row)}


def _closed(row: Any, keys: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(row, Mapping) or set(row) != keys:
        raise ValueError(f"{label} has missing or unmodeled fields")
    return row


def _text(value: Any, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ValueError(f"{label} must be text")
    return value


def _float(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be finite numeric")
    return float(value)


def _bool(value: Any, label: str) -> int:
    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")
    return int(value)


def project_entry_rules(
    rules: Mapping[str, Any], *, assignment_id: str, strategy_id: str,
    strategy_revision: int, snapshot_id: str, session: str,
) -> dict[str, list[dict[str, Any]]]:
    if (not assignment_id or strategy_id != STRATEGY_ID or type(strategy_revision) is not int
            or strategy_revision not in _REVISIONS):
        raise ValueError("entry rule strategy identity/revision is unsupported")
    UUID(snapshot_id)
    if not isinstance(session, str) or len(session) != 10:
        raise ValueError("entry rule session is invalid")
    if not isinstance(rules, Mapping) or set(rules) != set(_STAGES):
        raise ValueError("entry rule stages are missing or unmodeled")
    common = dict(assignment_id=assignment_id, strategy_id=strategy_id,
                  strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    stages: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    for stage in _STAGES:
        source = _closed(rules[stage], frozenset({"operator", "groups"}), "rule stage")
        operator = _text(source["operator"], "stage operator")
        if operator not in {"all", "any"} or not isinstance(source["groups"], list) or not source["groups"]:
            raise ValueError("entry rule stage operator/groups are invalid")
        group_rows: list[dict[str, Any]] = []
        seen_groups: set[str] = set()
        for group_ordinal, raw_group in enumerate(source["groups"]):
            group = _closed(raw_group, _GROUP, "rule group")
            group_id = _text(group["group_id"], "group_id")
            if group_id in seen_groups:
                raise ValueError("entry rule group IDs are duplicated")
            seen_groups.add(group_id)
            if not isinstance(group["conditions"], list) or not group["conditions"]:
                raise ValueError("entry rule group conditions are invalid")
            condition_rows: list[dict[str, Any]] = []
            seen_conditions: set[str] = set()
            for ordinal, raw_condition in enumerate(group["conditions"]):
                condition = _closed(raw_condition, _CONDITION, "rule condition")
                condition_id = _text(condition["condition_id"], "condition_id")
                if condition_id in seen_conditions:
                    raise ValueError("entry rule condition IDs are duplicated")
                seen_conditions.add(condition_id)
                value = condition["value"]
                condition_rows.append(_seal({
                    **common, "stage": stage, "group_ordinal": group_ordinal, "ordinal": ordinal,
                    "condition_id": condition_id,
                    "comparator": _text(condition["comparator"], "comparator"),
                    "enabled": _bool(condition["enabled"], "condition enabled"),
                    **{key: _text(condition[key], key, empty=True) for key in (
                        "left_source_id", "left_timeframe", "right_source_id", "right_timeframe")},
                    "value": None if value is None else _float(value, "condition value"),
                }))
            conditions.extend(condition_rows)
            group_rows.append(_seal({
                **common, "stage": stage, "ordinal": group_ordinal, "group_id": group_id,
                "label": _text(group["label"], "group label"),
                "operator": _text(group["operator"], "group operator"),
                "required_score": _float(group["required_score"], "required score"),
                "enabled": _bool(group["enabled"], "group enabled"),
                "condition_count": len(condition_rows),
                "condition_hash": _digest([row["content_hash"] for row in condition_rows]),
            }))
        groups.extend(group_rows)
        stages.append(_seal({
            **common, "stage": stage, "operator": operator,
            "group_count": len(group_rows),
            "group_hash": _digest([row["content_hash"] for row in group_rows]),
        }))
    return {"stage": stages, "group": groups, "condition": conditions}


def restore_entry_rules(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    if set(rows) != {"stage", "group", "condition"}:
        raise ValueError("entry rule typed families are incomplete")
    required = [{name for name, _ in table.columns} for table in TABLES]
    for family, keys in zip(("stage", "group", "condition"), required):
        if any(set(row) != keys for row in rows[family]):
            raise ValueError("entry rule typed columns differ")
    stage_index = {name: index for index, name in enumerate(_STAGES)}
    ordered_stages = sorted(rows["stage"], key=lambda row: stage_index.get(row["stage"], 99))
    if [row["stage"] for row in ordered_stages] != list(_STAGES):
        raise ValueError("entry rule stage set differs")
    first = ordered_stages[0]
    common = {key: first[key] for key, _ in _KEY}
    for family in rows.values():
        if any(any(row[key] != value for key, value in common.items()) for row in family):
            raise ValueError("entry rule snapshot fence differs")
    ordered_groups = sorted(rows["group"], key=lambda row: (stage_index.get(row["stage"], 99), row["ordinal"]))
    ordered_conditions = sorted(rows["condition"], key=lambda row: (
        stage_index.get(row["stage"], 99), row["group_ordinal"], row["ordinal"]))
    result: dict[str, Any] = {}
    for stage_row in ordered_stages:
        stage = stage_row["stage"]
        group_values: list[dict[str, Any]] = []
        stage_groups = [row for row in ordered_groups if row["stage"] == stage]
        if [row["ordinal"] for row in stage_groups] != list(range(len(stage_groups))):
            raise ValueError("entry rule group order differs")
        for group in stage_groups:
            child = [row for row in ordered_conditions
                     if row["stage"] == stage and row["group_ordinal"] == group["ordinal"]]
            if [row["ordinal"] for row in child] != list(range(len(child))):
                raise ValueError("entry rule condition order differs")
            group_values.append({
                "group_id": group["group_id"], "label": group["label"],
                "operator": group["operator"], "required_score": group["required_score"],
                "enabled": bool(group["enabled"]),
                "conditions": [{"condition_id": row["condition_id"],
                                "comparator": row["comparator"], "enabled": bool(row["enabled"]),
                                "left_source_id": row["left_source_id"],
                                "left_timeframe": row["left_timeframe"],
                                "right_source_id": row["right_source_id"],
                                "right_timeframe": row["right_timeframe"],
                                "value": row["value"]} for row in child],
            })
        result[stage] = {"operator": stage_row["operator"], "groups": group_values}
    projected = project_entry_rules(result, **common)
    if (projected["stage"] != ordered_stages or projected["group"] != ordered_groups
            or projected["condition"] != ordered_conditions):
        raise ValueError("entry rule content hash or family count mismatch")
    return result
