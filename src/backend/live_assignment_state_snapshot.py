"""Staged normalized assignment-state snapshot and exact cold child fence.

Only the closed state families in arte_assignment_state_composite are admitted.
The caller must attest the returned commit hash via the assignment base revision.
"""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime
from typing import Any, Mapping, Protocol

from src.backend.live_assignment_base_revision import recover_base_revision
from src.trading_runtime.arte_long_momentum_parameter_journal import (
    COMMIT_TABLE as PARAMETER_COMMIT_TABLE, ParameterStorage, SnapshotAdmission,
    load_attested_parameters,
)
from src.trading_runtime.strategy_engine import (
    AssignmentStatus, StrategyAssignment, StrategyPermissions,
)
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_assignment_state_composite import (
    project_modeled_assignment_state, restore_modeled_assignment_state,
)
from src.trading_runtime.arte_campaign_control_projection import TABLES as CAMPAIGN
from src.trading_runtime.arte_grouped_resistance_projection import TABLES as GROUPED
from src.trading_runtime.arte_long_momentum_squeeze_purchase_state import (
    LEDGER_TABLE, REQUEST_TABLE, KEY_TABLE, ADDED_LEVEL_TABLE,
)
from src.trading_runtime.arte_long_momentum_squeeze_clock_state import TABLE as CLOCK
from src.trading_runtime.arte_long_momentum_squeeze_progress_state import (
    MANIFEST_TABLE, MACD_TABLE, PROGRESS_TABLE, BROKEN_LEVEL_TABLE,
)
from src.trading_runtime.arte_long_momentum_squeeze_v7_evidence import (
    SET_TABLE, TABLES as V7_TABLES,
)
from src.trading_runtime.journal_contract import canonical_json


STATE_COMMIT = TableContract(
    "live_strategy_assignment_state_commit_typed_v1",
    (("assignment_id", "String"), ("revision", "UInt64"),
     ("snapshot_id", "UUID"), ("session", "Date"), ("run_id", "String"),
     ("grouped_present", "Bool"), ("child_count", "UInt32"),
     ("child_hash", "FixedString(64)"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, revision, snapshot_id",
)
_SPECS = (
    (CAMPAIGN[0], "campaign", "control"),
    (CAMPAIGN[1], "campaign", "policy"),
    *((table, "grouped_resistance", key) for table, key in zip(
        GROUPED, ("snapshot", "level", "member", "broken"))),
    (LEDGER_TABLE, "squeeze_purchase", "ledger"),
    (REQUEST_TABLE, "squeeze_purchase", "requests"),
    (KEY_TABLE, "squeeze_purchase", "request_keys"),
    (ADDED_LEVEL_TABLE, "squeeze_purchase", "successor_added_levels"),
    (CLOCK, "squeeze_clock", None),
    (MANIFEST_TABLE, "squeeze_progress", "manifest"),
    (MACD_TABLE, "squeeze_progress", "macd"),
    (PROGRESS_TABLE, "squeeze_progress", "progress"),
    (BROKEN_LEVEL_TABLE, "squeeze_progress", "broken_levels"),
    (SET_TABLE, "squeeze_v7_evidence", "set"),
    *((table, "squeeze_v7_evidence", family) for family, table in V7_TABLES.items()),
)
STATE_TABLES = tuple(table for table, _, _ in _SPECS) + (STATE_COMMIT,)
if len({table.name for table in STATE_TABLES}) != len(STATE_TABLES):
    raise AssertionError("assignment state table names must be unique")


class StateStorage(Protocol):
    def read(self, table: str, identity: Mapping[str, Any]) -> list[dict[str, Any]]: ...


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _order(rows: list[dict[str, Any]], table: TableContract) -> list[dict[str, Any]]:
    keys = [key.strip() for key in table.order.split(",")]
    if table in GROUPED and table is not GROUPED[0] and rows and "family" in rows[0]:
        rank = {"physical": 0, "known": 1, "break_rows": 2}
        return sorted(rows, key=lambda row: (rank[row["family"]],
                                            row.get("ordinal", 0),
                                            row.get("member_id", "")))
    if table in (REQUEST_TABLE, KEY_TABLE):
        rank = {"momentum": 0, "midpoint_add": 1}
        return sorted(rows, key=lambda row: (rank[row["request_kind"]],
                                            row["intent_id"], row.get("ordinal", 0)))
    if table is BROKEN_LEVEL_TABLE:
        rank = {"session": 0, "entry": 1}
        return sorted(rows, key=lambda row: (rank[row["owner"]], row["ordinal"]))
    return sorted(rows, key=lambda row: tuple(row[key] for key in keys))


def _flatten(projected: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result = {table.name: [] for table, _, _ in _SPECS}
    for table, group, key in _SPECS:
        value = projected[group]
        if group == "grouped_resistance":
            value = {} if value is None else value
        elif group == "squeeze_v7_evidence":
            values = []
            for path in sorted(value):
                evidence = value[path]
                if key == "set":
                    values.append(evidence["set"])
                elif evidence["set"]["evidence_family"] == key:
                    values.extend(evidence["rows"])
            value = values
        if group != "squeeze_v7_evidence":
            value = value.get(key, []) if key is not None else value
        rows = value if isinstance(value, list) else ([value] if value else [])
        result[table.name] = _order(list(rows), table)
    return result


def _commit(identity: Mapping[str, Any], rows: Mapping[str, list[dict[str, Any]]], *,
            grouped_present: bool) -> dict[str, Any]:
    manifest = [(table.name, len(rows[table.name]),
                 [row["content_hash"] for row in rows[table.name]])
                for table, _, _ in _SPECS]
    content = {**identity, "grouped_present": grouped_present,
               "child_count": sum(item[1] for item in manifest),
               "child_hash": _hash(manifest)}
    return {**content, "content_hash": _hash(content)}


def project_state_snapshot(state: Mapping[str, Any], *, run_id: str,
                           assignment_id: str, revision: int, snapshot_id: str,
                           session: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session, run_id=run_id)
    projected = project_modeled_assignment_state(state, **identity)
    rows = _flatten(projected)
    return rows, _commit(identity, rows,
                         grouped_present=projected["grouped_resistance"] is not None)


def recover_state_snapshot(storage: StateStorage, *, run_id: str,
                           assignment_id: str, revision: int, snapshot_id: str,
                           session: str, expected_commit_hash: str) -> dict[str, Any]:
    identity = dict(assignment_id=assignment_id, revision=revision,
                    snapshot_id=snapshot_id, session=session, run_id=run_id)
    commits = storage.read(STATE_COMMIT.name, identity)
    if len(commits) != 1 or commits[0].get("content_hash") != expected_commit_hash:
        raise ValueError("assignment state commit is missing, duplicated, or unattested")
    commit = commits[0]
    if set(commit) != {name for name, _ in STATE_COMMIT.columns}:
        raise ValueError("assignment state commit columns differ")
    rows = {table.name: _order(storage.read(table.name, identity), table)
            for table, _, _ in _SPECS}
    if _commit(identity, rows, grouped_present=commit["grouped_present"]) != commit:
        raise ValueError("assignment state child-family fence differs")
    campaign = {key: rows[table.name] for table, group, key in _SPECS
                if group == "campaign"}
    grouped = ({key: rows[table.name] for table, group, key in _SPECS
                if group == "grouped_resistance"} if commit["grouped_present"] else None)
    purchase = {key: rows[table.name] for table, group, key in _SPECS
                if group == "squeeze_purchase"}
    clock = rows[CLOCK.name]
    if len(clock) != 1:
        raise ValueError("assignment squeeze clock row count differs")
    progress = {key: rows[table.name] for table, group, key in _SPECS
                if group == "squeeze_progress"}
    for key in ("manifest", "progress"):
        if len(progress[key]) != 1:
            raise ValueError("assignment squeeze progress row count differs")
        progress[key] = progress[key][0]
    evidence = {}
    for parent in rows[SET_TABLE.name]:
        path, family = parent["source_path"], parent["evidence_family"]
        if family not in V7_TABLES or path in evidence:
            raise ValueError("assignment V7 evidence path or family differs")
        evidence[path] = {"set": parent,
                          "rows": [row for row in rows[V7_TABLES[family].name]
                                   if row["source_path"] == path]}
    projected = dict(campaign=campaign, grouped_resistance=grouped,
                     squeeze_purchase={**purchase, "ledger": purchase["ledger"][0]
                                       if len(purchase["ledger"]) == 1 else purchase["ledger"]},
                     squeeze_clock=clock[0], squeeze_progress=progress,
                     squeeze_v7_evidence=evidence)
    state = restore_modeled_assignment_state(projected, **identity)
    expected_rows, expected_commit = project_state_snapshot(state, **identity)
    if expected_rows != rows or expected_commit != commit:
        raise ValueError("assignment state exact reprojection differs")
    return state


def recover_attested_assignment(
    *, base_rows: list[Mapping[str, Any]], state_storage: StateStorage,
    parameter_storage: ParameterStorage, parameter_admission: SnapshotAdmission,
    assignment_id: str, revision_sequence: int, expected_base_hash: str,
    previous_revision_hash: str,
) -> StrategyAssignment:
    """Join a separately attested base head to both exact typed child commits.

    This is a cold reader only. The caller must obtain expected_base_hash from a
    stable Keeper owner/head; ClickHouse's latest visible row is not authority.
    """
    if len(base_rows) != 1:
        raise ValueError("assignment base revision missing or duplicated")
    base = dict(base_rows[0])
    row = recover_base_revision(
        base_rows, expected_assignment_id=assignment_id,
        expected_sequence=revision_sequence, expected_hash=expected_base_hash,
        parameter_content_hash=base.get("parameter_content_hash"),
        state_content_hash=base.get("state_content_hash"),
        previous_revision_hash=previous_revision_hash,
    )
    if row["strategy_revision"] < 26 or row["strategy_revision"] > 47:
        raise ValueError("assignment revision has no complete typed parameter family")
    parameters = load_attested_parameters(
        parameter_storage, parameter_admission, assignment_id=assignment_id,
        strategy_id=row["strategy_id"], strategy_revision=row["strategy_revision"],
        snapshot_id=row["parameter_snapshot_id"], session=row["parameter_session"],
    )
    if parameters is None:
        raise ValueError("assignment parameter snapshot missing")
    parameter_commits = parameter_storage.read(PARAMETER_COMMIT_TABLE.name, dict(
        assignment_id=assignment_id, strategy_id=row["strategy_id"],
        strategy_revision=row["strategy_revision"],
        snapshot_id=row["parameter_snapshot_id"],
        session=row["parameter_session"]))
    if (len(parameter_commits) != 1 or
            parameter_commits[0].get("content_hash") != row["parameter_content_hash"]):
        raise ValueError("assignment base parameter hash differs from attested commit")
    state = recover_state_snapshot(
        state_storage, run_id=row["state_run_id"], assignment_id=assignment_id,
        revision=revision_sequence, snapshot_id=row["state_snapshot_id"],
        session=row["state_session"],
        expected_commit_hash=row["state_content_hash"],
    )
    return StrategyAssignment(
        assignment_id=assignment_id, strategy_id=row["strategy_id"],
        strategy_revision=row["strategy_revision"], account_id=row["account_id"],
        ticker=row["ticker"], conid=row["conid"],
        status=AssignmentStatus(row["status"]),
        permissions=StrategyPermissions(
            observe=row["can_observe"], enter=row["can_enter"], add=row["can_add"],
            reduce=row["can_reduce"], exit=row["can_exit"], reenter=row["can_reenter"]),
        parameters=parameters, state=state, source=row["source"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
