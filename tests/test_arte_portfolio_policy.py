from __future__ import annotations

from dataclasses import replace
import json

import pytest

from src.trading_runtime.arte_portfolio_policy import (
    _policy_rows, load_portfolio_policy, publish_portfolio_policy,
)
from src.trading_runtime.portfolio import PortfolioPolicy


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
                              "trading_portfolio_policy_allowed_v1",
                              "trading_portfolio_policy_commit_v1"]


def test_policy_catalog_ignores_unfenced_rows_and_detects_tampering() -> None:
    policy = PortfolioPolicy(policy_id="test")
    client = Catalog()
    policy_hash, root, _ = _policy_rows(policy)
    client.tables["trading_portfolio_policy_v1"] = [root]
    assert load_portfolio_policy(client, policy_hash) is None
    assert publish_portfolio_policy(client, policy) == policy_hash
    client.tables["trading_portfolio_policy_allowed_v1"][0]["value"] = "WRONG"
    with pytest.raises(RuntimeError, match="allowed rows differ|content differs"):
        load_portfolio_policy(client, policy_hash)
    with pytest.raises(RuntimeError):
        publish_portfolio_policy(client, policy)


def test_policy_catalog_rejects_unrepresentable_allowed_values() -> None:
    policy = replace(PortfolioPolicy(), allowed_currencies=("USD", "USD"))
    with pytest.raises(ValueError, match="unique string tuple"):
        _policy_rows(policy)
