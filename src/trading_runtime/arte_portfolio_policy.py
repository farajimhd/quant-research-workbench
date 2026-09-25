"""Immutable, normalized portfolio policies shared by live and Backtest.

The policy catalog is not a mutable account snapshot. A policy is published
once by content hash, with its five allowed-value lists in child rows and a
commit fence written last. A run/account selection will reference this hash.
"""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any

from src.trading_runtime.arte_journal_schema import (
    POLICY_ALLOWED_FIELDS, POLICY_ALLOWED_TABLES, POLICY_BOOLEAN_FIELDS, POLICY_INTEGER_FIELDS,
    POLICY_NUMERIC_FIELDS, journal_permission_preflight, storage_preflight,
)
from src.trading_runtime.arte_journal_writer import _insert, _literal, _rows, _wire_row
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.portfolio import PortfolioPolicy


_POLICY = "trading_portfolio_policy_v1"
_COMMIT = "trading_portfolio_policy_commit_v2"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FIELD_ORDER = {name: index for index, name in enumerate(POLICY_ALLOWED_FIELDS)}


def _child_rows(children: tuple[dict[str, Any], ...]) -> dict[str, tuple[dict[str, Any], ...]]:
    """Route finite policy lists to named relations, never a persisted EAV row."""
    return {
        table: tuple({"policy_hash": row["policy_hash"], "ordinal": row["ordinal"],
                      column: row["value"]}
                     for row in children if row["field_name"] == name)
        for name, (table, column) in POLICY_ALLOWED_TABLES.items()
    }


def _digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _policy_rows(policy: PortfolioPolicy) -> tuple[str, dict[str, Any], tuple[dict[str, Any], ...]]:
    expected = ({"policy_id", "revision"} | set(POLICY_NUMERIC_FIELDS)
                | set(POLICY_INTEGER_FIELDS) | set(POLICY_BOOLEAN_FIELDS)
                | set(POLICY_ALLOWED_FIELDS))
    if {field.name for field in fields(PortfolioPolicy)} != expected:
        raise ValueError("Portfolio policy has fields absent from its typed catalog")
    if not isinstance(policy, PortfolioPolicy):
        raise TypeError("Typed policy publication requires PortfolioPolicy")
    root: dict[str, Any] = {"policy_id": policy.policy_id, "revision": policy.revision}
    for name in POLICY_NUMERIC_FIELDS:
        root[name] = str(getattr(policy, name))
    for name in POLICY_INTEGER_FIELDS:
        root[name] = getattr(policy, name)
    for name in POLICY_BOOLEAN_FIELDS:
        root[name] = int(getattr(policy, name))
    allowed: list[dict[str, Any]] = []
    for name in POLICY_ALLOWED_FIELDS:
        values = getattr(policy, name)
        if (not isinstance(values, tuple) or len(values) > 65535
                or any(not isinstance(value, str) or not value for value in values)
                or len(set(values)) != len(values)):
            raise ValueError(f"Portfolio policy {name} is not a unique string tuple")
        allowed.extend({"field_name": name, "ordinal": index, "value": value}
                       for index, value in enumerate(values))
    # Hash the exact Decimal(38,18) representation stored in ClickHouse.
    normalized = _wire_row(_POLICY, {"policy_hash": "0" * 64, **root})
    normalized.pop("policy_hash")
    policy_hash = _digest({"root": normalized, "allowed": allowed})
    return policy_hash, {"policy_hash": policy_hash, **normalized}, tuple(
        {"policy_hash": policy_hash, **row} for row in allowed
    )


def _query_rows(client: Any, table: str, policy_hash: str) -> list[dict[str, Any]]:
    from src.trading_runtime.arte_journal_writer import _CONTRACTS

    columns = ",".join(column for column, _ in _CONTRACTS[table].columns)
    return _rows(client, f"SELECT {columns} FROM arte.{table} "
                 f"WHERE policy_hash={_literal(policy_hash)} FORMAT JSONEachRow")


def load_portfolio_policy(client: Any, policy_hash: str) -> PortfolioPolicy | None:
    """Return only a complete, hash-verified immutable policy."""
    if not _HASH.fullmatch(policy_hash):
        raise ValueError("Portfolio policy hash is invalid")
    commits = _query_rows(client, _COMMIT, policy_hash)
    if not commits:
        return None
    roots = _query_rows(client, _POLICY, policy_hash)
    children = []
    for name, (table, column) in POLICY_ALLOWED_TABLES.items():
        for row in _query_rows(client, table, policy_hash):
            if set(row) != {"policy_hash", "ordinal", column}:
                raise RuntimeError("Portfolio policy allowed rows differ from typed schema")
            children.append({"policy_hash": row["policy_hash"], "field_name": name,
                             "ordinal": row["ordinal"], "value": row[column]})
    if len(commits) != 1 or len(roots) != 1:
        raise RuntimeError("Portfolio policy fence has missing or duplicate root rows")
    root, commit = roots[0], commits[0]
    if (set(root) != {"policy_hash", "policy_id", "revision"}
            | set(POLICY_NUMERIC_FIELDS) | set(POLICY_INTEGER_FIELDS)
            | set(POLICY_BOOLEAN_FIELDS)
            or set(commit) != {"policy_hash", "allowed_count", "allowed_hash", "committed_at"}
            or str(root["policy_hash"]) != policy_hash
            or str(commit["policy_hash"]) != policy_hash):
        raise RuntimeError("Portfolio policy rows differ from their typed schema")
    ordered = sorted(children, key=lambda row: (
        _FIELD_ORDER.get(str(row.get("field_name")), len(_FIELD_ORDER)),
        int(row.get("ordinal", -1)),
    ))
    values: dict[str, list[str]] = {name: [] for name in POLICY_ALLOWED_FIELDS}
    tuples: list[dict[str, Any]] = []
    for row in ordered:
        name = str(row.get("field_name"))
        if (set(row) != {"policy_hash", "field_name", "ordinal", "value"}
                or row["policy_hash"] != policy_hash or name not in values
                or int(row["ordinal"]) != len(values[name])
                or not isinstance(row["value"], str) or not row["value"]):
            raise RuntimeError("Portfolio policy allowed rows are incomplete or duplicated")
        values[name].append(row["value"])
        tuples.append({"field_name": name, "ordinal": int(row["ordinal"]),
                       "value": row["value"]})
    if (any(len(items) != len(set(items)) for items in values.values())
            or int(commit["allowed_count"]) != len(tuples)
            or str(commit["allowed_hash"]) != _digest(tuples)):
        raise RuntimeError("Portfolio policy allowed rows differ from their fence")
    normalized_root = {key: value for key, value in root.items() if key != "policy_hash"}
    if _digest({"root": normalized_root, "allowed": tuples}) != policy_hash:
        raise RuntimeError("Portfolio policy content differs from its hash")
    kwargs: dict[str, Any] = {
        "policy_id": root["policy_id"], "revision": int(root["revision"]),
        **{name: float(root[name]) for name in POLICY_NUMERIC_FIELDS},
        **{name: int(root[name]) for name in POLICY_INTEGER_FIELDS},
        **{name: bool(int(root[name])) for name in POLICY_BOOLEAN_FIELDS},
        **{name: tuple(items) for name, items in values.items()},
    }
    if any(int(root[name]) not in (0, 1) for name in POLICY_BOOLEAN_FIELDS):
        raise RuntimeError("Portfolio policy boolean is outside its typed domain")
    return PortfolioPolicy(**kwargs)


def publish_portfolio_policy(client: Any, policy: PortfolioPolicy) -> str:
    """Retry-safe control-plane publication, never on a market-data callback."""
    policy_hash, root, children = _policy_rows(policy)
    dispatch = getattr(client, "typed_insert_dispatch", None)
    families = ((_POLICY, (root,)), *_child_rows(children).items())
    existing_commit = _query_rows(client, _COMMIT, policy_hash)
    if existing_commit:
        if load_portfolio_policy(client, policy_hash) != policy:
            raise RuntimeError("Committed portfolio policy differs from its source")
        if dispatch is not None:
            status = dispatch.begin_policy_publication(
                policy_hash=policy_hash, has_ch_rows=True)
            if status == "publishing":
                _seal_policy_publication(
                    dispatch, policy_hash, families, existing_commit)
            else:
                dispatch.assert_policy_receipt(
                    policy_hash=policy_hash,
                    fence_hash=_policy_fence_hash(existing_commit))
        return policy_hash
    actual_families = {
        table: _query_rows(client, table, policy_hash)
        for table, _ in families
    }
    if dispatch is not None:
        status = dispatch.begin_policy_publication(
            policy_hash=policy_hash,
            has_ch_rows=any(actual_families.values()))
        if status != "publishing":
            raise RuntimeError("Committed policy dispatch gate lacks ClickHouse fence")
    for table, expected in families:
        actual = actual_families[table]
        expected_wire = [_wire_row(table, row) for row in expected]
        if actual and sorted(actual, key=canonical_json) != sorted(expected_wire, key=canonical_json):
            raise RuntimeError(f"Portfolio policy {table} has conflicting partial rows")
        if not actual and expected:
            kwargs = ({"dispatch_policy_hash": policy_hash}
                      if dispatch is not None else {})
            _insert(client, table, expected,
                    f"portfolio-policy:{policy_hash}:{table}", **kwargs)
            actual = _query_rows(client, table, policy_hash)
        if sorted(actual, key=canonical_json) != sorted(expected_wire, key=canonical_json):
            raise RuntimeError(f"Portfolio policy {table} did not become durable")
    allowed_hash = _digest(tuple(
        {key: row[key] for key in ("field_name", "ordinal", "value")}
        for row in children
    ))
    fence = {"policy_hash": policy_hash, "allowed_count": len(children),
             "allowed_hash": allowed_hash,
             "committed_at": datetime.now(timezone.utc).isoformat()}
    kwargs = ({"dispatch_policy_hash": policy_hash}
              if dispatch is not None else {})
    _insert(client, _COMMIT, (fence,),
            f"portfolio-policy:{policy_hash}:commit", **kwargs)
    if load_portfolio_policy(client, policy_hash) != policy:
        raise RuntimeError("Portfolio policy fence did not become durable")
    if dispatch is not None:
        commits = _query_rows(client, _COMMIT, policy_hash)
        _seal_policy_publication(dispatch, policy_hash, families, commits)
    return policy_hash


def _seal_policy_publication(
    dispatch: Any, policy_hash: str,
    families: tuple[tuple[str, tuple[dict[str, Any], ...]], ...],
    commits: list[dict[str, Any]],
) -> None:
    fence_hash = _policy_fence_hash(commits)
    operations = tuple((table, f"portfolio-policy:{policy_hash}:{table}")
                       for table, expected in families if expected) + ((
        _COMMIT, f"portfolio-policy:{policy_hash}:commit"),)
    for table, token in operations:
        dispatch.seal_verified_policy_operation(
            policy_hash=policy_hash, table=table, token=token)
    dispatch.compact_verified_policy(
        policy_hash=policy_hash, fence_hash=fence_hash,
        operations=operations)


def _policy_fence_hash(commits: list[dict[str, Any]]) -> str:
    if len(commits) != 1 or set(commits[0]) != {
            "policy_hash", "allowed_count", "allowed_hash", "committed_at"}:
        raise RuntimeError("Portfolio policy fence is absent, duplicate, or malformed")
    stable = {key: value for key, value in commits[0].items()
              if key != "committed_at"}
    return _digest(stable)


def load_attested_portfolio_policy(
    client: Any, dispatch: Any, policy_hash: str,
) -> PortfolioPolicy | None:
    """Cold read only an exact committed CH policy with its Keeper receipt."""
    policy = load_portfolio_policy(client, policy_hash)
    if policy is None:
        dispatch.assert_policy_absent(policy_hash=policy_hash)
        return None
    dispatch.assert_policy_receipt(
        policy_hash=policy_hash,
        fence_hash=_policy_fence_hash(_query_rows(client, _COMMIT, policy_hash)))
    return policy


class ArtePortfolioPolicyStore:
    """Control-plane catalog with startup storage and grant verification."""

    def __init__(self, client: Any) -> None:
        storage_preflight(client)
        journal_permission_preflight(client)
        self.client = client

    def publish(self, policy: PortfolioPolicy) -> str:
        return publish_portfolio_policy(self.client, policy)

    def load(self, policy_hash: str) -> PortfolioPolicy | None:
        return load_portfolio_policy(self.client, policy_hash)
