from __future__ import annotations

from dataclasses import replace
import json

import pytest

from src.trading_runtime.arte_portfolio_policy import (
    _policy_rows, load_attested_portfolio_policy,
    load_portfolio_policy, publish_portfolio_policy,
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


def test_cold_policy_requires_exact_keeper_receipt_and_rejects_orphan() -> None:
    from test_arte_typed_insert_dispatch import Keeper
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_ownership import KeeperUnavailable

    policy = PortfolioPolicy(policy_id="cold-proof")
    client = Catalog()
    policy_hash = publish_portfolio_policy(client, policy)
    authority = TypedInsertDispatch(Keeper())
    with pytest.raises(KeeperUnavailable, match="receipt differs"):
        load_attested_portfolio_policy(client, authority, policy_hash)
    other_hash = "a" * 64
    authority.begin_policy_publication(policy_hash=other_hash, has_ch_rows=False)
    with pytest.raises(KeeperUnavailable, match="lacks ClickHouse fence"):
        load_attested_portfolio_policy(client, authority, other_hash)


def test_strict_policy_publisher_compacts_all_relations_and_retries() -> None:
    from test_arte_typed_insert_dispatch import Keeper
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    policy = replace(
        PortfolioPolicy(policy_id="strict-all"),
        allowed_security_types=("STK",), allowed_currencies=("USD",),
        restricted_symbols=("BAD",), allowed_execution_policies=("market",),
        allowed_protection_profiles=("bracket",),
    )
    authority = TypedInsertDispatch(Keeper(), max_operations=7)
    class StrictCatalog(Catalog):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def __init__(self) -> None:
            super().__init__()
            self.query_ids: list[str] = []
        def execute(self, sql: str, *, query_id=None) -> str:
            if sql.startswith("INSERT "):
                self.query_ids.append(query_id)
            return super().execute(sql)
    client = StrictCatalog()
    policy_hash = publish_portfolio_policy(client, policy)
    assert len(client.inserts) == 7
    assert len(set(client.query_ids)) == 7
    assert all(value and value.startswith("arte_typed_") for value in client.query_ids)
    gate = authority._read_policy_gate(policy_hash)[0]
    assert (gate.mode, gate.inflight, gate.registered) == ("committed", 0, 0)
    assert load_attested_portfolio_policy(client, authority, policy_hash) == policy
    assert publish_portfolio_policy(client, policy) == policy_hash
    assert len(client.inserts) == 7


def test_strict_policy_recovers_after_fence_readback_before_keeper_seal() -> None:
    from test_arte_typed_insert_dispatch import Keeper
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_ownership import KeeperUnavailable

    policy = PortfolioPolicy(policy_id="seal-retry")
    authority = TypedInsertDispatch(Keeper())
    class StrictCatalog(Catalog):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        def execute(self, sql: str, *, query_id=None) -> str:
            return super().execute(sql)
    client = StrictCatalog()
    seal = authority.seal_verified_policy_operation
    authority.seal_verified_policy_operation = lambda **_kwargs: (
        _ for _ in ()).throw(OSError("crash after ClickHouse fence readback"))
    with pytest.raises(OSError, match="after ClickHouse fence"):
        publish_portfolio_policy(client, policy)
    before = tuple(client.inserts)
    policy_hash = _policy_rows(policy)[0]
    with pytest.raises(KeeperUnavailable, match="receipt differs"):
        load_attested_portfolio_policy(client, authority, policy_hash)
    authority.seal_verified_policy_operation = seal
    assert publish_portfolio_policy(client, policy) == policy_hash
    assert tuple(client.inserts) == before
    assert load_attested_portfolio_policy(client, authority, policy_hash) == policy


def test_strict_policy_lost_response_stays_pending_after_late_row() -> None:
    from test_arte_typed_insert_dispatch import Keeper
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from src.trading_runtime.keeper_ownership import KeeperUnavailable

    policy = PortfolioPolicy(policy_id="lost-response")
    authority = TypedInsertDispatch(Keeper())
    class LostResponseCatalog(Catalog):
        typed_insert_strict = True
        typed_insert_dispatch = authority
        lose_once = True
        def execute(self, sql: str, *, query_id=None) -> str:
            response = super().execute(sql)
            if sql.startswith("INSERT ") and self.lose_once:
                self.lose_once = False
                raise TimeoutError("lost response after possible commit")
            return response
    client = LostResponseCatalog()
    with pytest.raises(TimeoutError, match="lost response"):
        publish_portfolio_policy(client, policy)
    assert client.inserts == ["trading_portfolio_policy_v1"]
    with pytest.raises(KeeperUnavailable, match="operation identity differs"):
        publish_portfolio_policy(client, policy)
    policy_hash = _policy_rows(policy)[0]
    with pytest.raises(KeeperUnavailable, match="receipt differs"):
        load_attested_portfolio_policy(client, authority, policy_hash)
