from __future__ import annotations

from dataclasses import replace
import json

import pytest

from src.trading_runtime.arte_portfolio_policy import (
    _policy_rows, load_portfolio_policy, publish_portfolio_policy,
)
from src.trading_runtime.portfolio import PortfolioPolicy
from src.trading_runtime.arte_journal_schema import POLICY_ALLOWED_TABLES


class Catalog:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.tables.setdefault(name, []).extend(
                json.loads(line) for line in sql.split("\n", 1)[1].splitlines()
            )
            return ""
        assert sql.startswith("SELECT ") and "WHERE policy_hash='" in sql
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        policy_hash = sql.split("WHERE policy_hash='", 1)[1].split("'", 1)[0]
        return "\n".join(json.dumps(row) for row in self.tables.get(name, [])
                         if row["policy_hash"] == policy_hash)


def test_policy_catalog_is_typed_and_round_trips_without_json_columns() -> None:
    policy = replace(PortfolioPolicy(policy_id="test", revision=3),
                     eligible_equity_fraction=1 / 3,
                     restricted_symbols=("AAA", "BBB"))
    policy_hash, root, allowed = _policy_rows(policy)
    assert len(policy_hash) == 64
    assert root["policy_hash"] == policy_hash
    assert all(not isinstance(value, (dict, list, tuple)) for value in root.values())
    assert [row["value"] for row in allowed if row["field_name"] == "restricted_symbols"] == [
        "AAA", "BBB",
    ]
    client = Catalog()
    assert publish_portfolio_policy(client, policy) == policy_hash
    assert load_portfolio_policy(client, policy_hash) == policy
    assert publish_portfolio_policy(client, policy) == policy_hash
    assert client.inserts == ["trading_portfolio_policy_v1",
                              *[table for table, _ in POLICY_ALLOWED_TABLES.values()],
                              "trading_portfolio_policy_commit_v2"]


def test_policy_catalog_ignores_unfenced_rows_and_detects_tampering() -> None:
    policy = PortfolioPolicy(policy_id="test")
    client = Catalog()
    policy_hash, root, _ = _policy_rows(policy)
    client.tables["trading_portfolio_policy_v1"] = [root]
    assert load_portfolio_policy(client, policy_hash) is None
    assert publish_portfolio_policy(client, policy) == policy_hash
    client.tables["trading_portfolio_policy_currency_v1"][0]["currency"] = "WRONG"
    with pytest.raises(RuntimeError, match="allowed rows differ|content differs"):
        load_portfolio_policy(client, policy_hash)
    with pytest.raises(RuntimeError):
        publish_portfolio_policy(client, policy)


def test_policy_catalog_rejects_unrepresentable_allowed_values() -> None:
    policy = replace(PortfolioPolicy(), allowed_currencies=("USD", "USD"))
    with pytest.raises(ValueError, match="unique string tuple"):
        _policy_rows(policy)


def test_each_allowed_policy_domain_has_its_own_relation() -> None:
    policy = replace(
        PortfolioPolicy(policy_id="five-domains"),
        allowed_security_types=("STK",),
        allowed_currencies=("USD", "CAD"),
        restricted_symbols=("BAD",),
        allowed_execution_policies=("market",),
        allowed_protection_profiles=("bracket",),
    )
    client = Catalog()
    policy_hash = publish_portfolio_policy(client, policy)
    for name, (table, column) in POLICY_ALLOWED_TABLES.items():
        rows = client.tables[table]
        assert [row[column] for row in rows] == list(getattr(policy, name))
        assert all(set(row) == {"policy_hash", "ordinal", column}
                   and row["policy_hash"] == policy_hash for row in rows)
    assert "trading_portfolio_policy_allowed_v1" not in client.tables
    assert load_portfolio_policy(client, policy_hash) == policy
    client.tables["trading_portfolio_policy_currency_v1"].append(
        dict(client.tables["trading_portfolio_policy_currency_v1"][0]))
    with pytest.raises(RuntimeError, match="incomplete or duplicated"):
        load_portfolio_policy(client, policy_hash)
