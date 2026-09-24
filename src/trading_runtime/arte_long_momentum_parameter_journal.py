"""Staged typed Long Momentum parameter publication and cold recovery.

Operator DDL is explicit and off the active journal's TABLES/startup path.
The storage client is injected; this module never creates a connection.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Protocol

from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.arte_long_momentum_entry_confirm_parameters import TABLES as CONFIRM_TABLES
from src.trading_runtime.arte_long_momentum_entry_rules import TABLES as RULE_TABLES
from src.trading_runtime.arte_long_momentum_execution_parameters import TABLE as EXECUTION_TABLE
from src.trading_runtime.arte_long_momentum_exit_parameters import TABLES as EXIT_TABLES
from src.trading_runtime.arte_long_momentum_lifecycle_parameters import TABLES as LIFECYCLE_TABLES
from src.trading_runtime.arte_long_momentum_parameters import (
    project_long_momentum_parameters, restore_long_momentum_parameters,
)
from src.trading_runtime.arte_long_momentum_sizing_protection_parameters import (
    RISK_MULTIPLE_TABLE, TABLES as PROTECTION_TABLES,
)
from src.trading_runtime.arte_long_momentum_structure_parameters import TABLES as STRUCTURE_TABLES
from src.trading_runtime.journal_contract import canonical_json


_FAMILIES: tuple[tuple[TableContract, str, str | None], ...] = (
    *((table, "entry_confirm", family) for family, table in CONFIRM_TABLES.items()),
    *((table, "sizing_protection", family) for family, table in PROTECTION_TABLES.items()),
    (RISK_MULTIPLE_TABLE, "sizing_protection", "risk_multiples"),
    (EXECUTION_TABLE, "execution", None),
    *((table, "lifecycle", family) for family, table in zip(
        ("add", "reentry", "profit_pocket"), LIFECYCLE_TABLES)),
    *((table, "exit", family) for family, table in zip(
        ("final_exit", "exit_route"), EXIT_TABLES)),
    *((table, "structure", family) for family, table in zip(
        ("structural_entry", "momentum_management"), STRUCTURE_TABLES)),
    *((table, "entry_rules", family) for family, table in zip(
        ("stage", "group", "condition"), RULE_TABLES)),
)
COMMIT_TABLE = TableContract(
    "trading_assignment_long_momentum_parameter_commit_v1",
    (("assignment_id", "String"), ("strategy_id", "String"),
     ("strategy_revision", "UInt32"), ("snapshot_id", "UUID"), ("session", "Date"),
     ("child_count", "UInt32"), ("child_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "assignment_id, strategy_revision, snapshot_id",
)
PARAMETER_TABLES = tuple(table for table, _, _ in _FAMILIES) + (COMMIT_TABLE,)
if len({table.name for table in PARAMETER_TABLES}) != len(PARAMETER_TABLES):
    raise AssertionError("duplicate Long Momentum parameter table")


class ParameterStorage(Protocol):
    def insert(self, table: str, rows: list[dict[str, Any]]) -> None: ...
    def read(self, table: str, identity: Mapping[str, Any]) -> list[dict[str, Any]]: ...


class SnapshotAdmission(Protocol):
    def begin_once(self, identity: Mapping[str, Any]) -> bool:
        """Durably, atomically claim this snapshot exactly once across restarts."""

    def mark_committed(self, identity: Mapping[str, Any], content_hash: str) -> None:
        """Record reconciliation; never reopen the claim for child inserts."""

    def assert_current(self, identity: Mapping[str, Any]) -> None:
        """Fail closed if the external owner holder/epoch is no longer current."""

    def read_claim(self, identity: Mapping[str, Any]) -> Any: ...


class UncommittedParameterSnapshot(RuntimeError):
    """An incomplete/ambiguous snapshot needs operator reconciliation."""


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _identity(*, assignment_id: str, strategy_id: str, strategy_revision: int,
              snapshot_id: str, session: str) -> dict[str, Any]:
    return dict(assignment_id=assignment_id, strategy_id=strategy_id,
                strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)


def _table_rows(projected: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table, group, family in _FAMILIES:
        value = projected[group] if family is None else projected[group][family]
        result[table.name] = list(value) if isinstance(value, list) else [value]
    return result


def _child_fence(rows: Mapping[str, list[dict[str, Any]]]) -> tuple[int, str]:
    manifests = []
    for table, _, _ in _FAMILIES:
        family = rows[table.name]
        manifests.append((table.name, len(family), sorted(row["content_hash"] for row in family)))
    return sum(item[1] for item in manifests), _digest(manifests)


def _commit(identity: Mapping[str, Any], rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    count, digest = _child_fence(rows)
    content = {**identity, "child_count": count, "child_hash": digest}
    return {**content, "content_hash": _digest(content)}


def _reconstruct(rows: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for table, group, family in _FAMILIES:
        values = rows[table.name]
        if family is None:
            if len(values) != 1:
                raise ValueError(f"{table.name} row count is invalid")
            result[group] = values[0]
            continue
        result.setdefault(group, {})
        if table.name == RISK_MULTIPLE_TABLE.name:
            values = sorted(values, key=lambda row: row["ordinal"])
        if group == "entry_rules" and family in {"stage", "group", "condition"}:
            result[group][family] = values
        elif group == "exit":
            result[group][family] = values
        elif family == "risk_multiples":
            result[group][family] = values
        else:
            if len(values) != 1:
                raise ValueError(f"{table.name} row count is invalid")
            result[group][family] = values[0]
    return result


def load_diagnostic_parameters(storage: ParameterStorage, *, assignment_id: str,
                               strategy_id: str, strategy_revision: int,
                               snapshot_id: str, session: str) -> dict[str, Any] | None:
    """Inspect CH-only fence; diagnostic evidence, never trading authority."""
    identity = _identity(assignment_id=assignment_id, strategy_id=strategy_id,
                         strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    commits = storage.read(COMMIT_TABLE.name, identity)
    if not commits:
        return None
    if len(commits) != 1 or set(commits[0]) != {name for name, _ in COMMIT_TABLE.columns}:
        raise ValueError("parameter commit is duplicated or malformed")
    commit = commits[0]
    if any(commit[key] != value for key, value in identity.items()):
        raise ValueError("parameter commit snapshot identity differs")
    if _digest({key: value for key, value in commit.items() if key != "content_hash"}) != commit["content_hash"]:
        raise ValueError("parameter commit content hash mismatch")
    rows = {table.name: storage.read(table.name, identity) for table, _, _ in _FAMILIES}
    if _commit(identity, rows) != commit:
        raise ValueError("parameter child-family count/hash fence mismatch")
    return restore_long_momentum_parameters(_reconstruct(rows), **identity)


def load_attested_parameters(storage: ParameterStorage, admission: SnapshotAdmission, *,
                             assignment_id: str, strategy_id: str, strategy_revision: int,
                             snapshot_id: str, session: str) -> dict[str, Any] | None:
    """Cold trading read requires matching CH and persistent Keeper proof."""
    identity = _identity(assignment_id=assignment_id, strategy_id=strategy_id,
                         strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    claim = admission.read_claim(identity)
    parameters = load_diagnostic_parameters(storage, **identity)
    if claim is None and parameters is None:
        return None
    if claim is None or claim.state != "committed" or parameters is None:
        raise UncommittedParameterSnapshot(
            "parameter snapshot lacks a committed Keeper claim and verified CH fence"
        )
    commits = storage.read(COMMIT_TABLE.name, identity)
    if len(commits) != 1 or claim.content_hash != commits[0]["content_hash"]:
        raise UncommittedParameterSnapshot("Keeper claim hash differs from CH commit")
    # A historical owner epoch remains valid for immutable cold recovery.
    return parameters


def publish_parameters(storage: ParameterStorage, parameters: Mapping[str, Any], *,
                       admission: SnapshotAdmission,
                       assignment_id: str, strategy_id: str, strategy_revision: int,
                       snapshot_id: str, session: str) -> dict[str, Any]:
    """Publish children, verify exact readback, then publish durable fence last."""
    identity = _identity(assignment_id=assignment_id, strategy_id=strategy_id,
                         strategy_revision=strategy_revision, snapshot_id=snapshot_id, session=session)
    projected = project_long_momentum_parameters(parameters, **identity)
    expected = _table_rows(projected)
    commit = _commit(identity, expected)
    prior = storage.read(COMMIT_TABLE.name, identity)
    if prior:
        recovered = load_attested_parameters(storage, admission, **identity)
        if recovered != parameters or len(prior) != 1 or prior[0]["content_hash"] != commit["content_hash"]:
            raise ValueError("parameter snapshot identity already committed different content")
        return commit
    # MergeTree does not enforce a unique key. A persistent single-use claim
    # makes an uncertain first attempt non-retryable, even if a delayed insert
    # has not become visible yet. The actual Keeper implementation is not wired.
    if not admission.begin_once(identity):
        raise UncommittedParameterSnapshot(
            "parameter snapshot was already attempted; reconcile before new publication"
        )
    preexisting = {table.name: storage.read(table.name, identity) for table, _, _ in _FAMILIES}
    if any(preexisting.values()):
        raise UncommittedParameterSnapshot(
            "parameter snapshot has uncommitted child rows; operator reconciliation required"
        )
    try:
        for table, _, _ in _FAMILIES:
            values = expected[table.name]
            if values:
                admission.assert_current(identity)
                storage.insert(table.name, values)
        readback = {table.name: storage.read(table.name, identity) for table, _, _ in _FAMILIES}
        if _commit(identity, readback) != commit:
            raise ValueError("parameter child readback differs before commit")
        restored = restore_long_momentum_parameters(_reconstruct(readback), **identity)
        if restored != parameters:
            raise ValueError("parameter child recovery differs before commit")
        admission.assert_current(identity)
        storage.insert(COMMIT_TABLE.name, [commit])
    except Exception:
        recovered = load_diagnostic_parameters(storage, **identity)
        if recovered != parameters:
            raise
    final = load_diagnostic_parameters(storage, **identity)
    if final != parameters:
        raise ValueError("parameter commit receipt was not durable")
    admission.mark_committed(identity, commit["content_hash"])
    if load_attested_parameters(storage, admission, **identity) != parameters:
        raise UncommittedParameterSnapshot("parameter attested receipt was not durable")
    return commit
