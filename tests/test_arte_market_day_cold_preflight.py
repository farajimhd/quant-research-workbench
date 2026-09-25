from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate, certified_market_day_plan_from_cold_audit,
    cold_certified_market_day_plan, discover_cold_certified_market_day_plan,
    replay_canonical_source_plan_parity,
    verify_attested_market_products,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
from src.trading_runtime.arte_market_day_source_plan import (
    TABLES as SOURCE_TABLES, recover_source_plan,
)
from test_arte_market_day_certification import BUILD, DAY, inventory
from test_arte_market_day_keeper import FakeKeeper
from test_arte_market_day_publisher import FakeClickHouse


def fixture():
    client = FakeClickHouse()
    prepared = inventory()
    client.rows = deepcopy(prepared)
    keeper = MarketDayKeeperAuthority(FakeKeeper())
    claim = keeper.acquire(BUILD, "worker")
    assert claim is not None
    fence = prepared["market_day_build_fence_v1"][0]
    source_rows = {table.name: prepared[table.name] for table in SOURCE_TABLES}
    source_plan = recover_source_plan(source_rows, BUILD,
        expected_hash=fence["source_plan_hash"])
    with patch("scripts.build_market_day.source_plan", return_value=source_plan):
        keeper.verify_source(claim, object(), source_plan,
                             expected_hash=fence["source_plan_hash"])
    keeper.attest(claim, definition_hash=fence["definition_hash"],
                  source_plan_hash=fence["source_plan_hash"],
                  source_inventory_hash=fence["source_inventory_hash"],
                  header_hash=fence["header_hash"],
                  scope_hash=fence["scope_hash"],
                  stage_hash=fence["stage_hash"],
                  seed_hash=fence["seed_hash"])
    return client, keeper


def test_cold_read_accepts_exact_historical_proof_but_not_backtest_cutover() -> None:
    client, keeper = fixture()
    audit = audit_attested_market_day_certificate(client, keeper, BUILD,
                                                   sessions=(DAY,))
    assert audit.certificate.scopes == ((DAY, "TEST"),)
    assert audit.certificate_parts_on_ssd
    assert audit.source_storage_verified
    assert not audit.fixed_backtest_ready
    assert not client.inserts


def test_cold_read_rejects_unattested_and_mismatched_fence() -> None:
    client, _ = fixture()
    no_proof = MarketDayKeeperAuthority(FakeKeeper())
    with pytest.raises(RuntimeError, match="Keeper CAS attestation"):
        audit_attested_market_day_certificate(client, no_proof, BUILD,
                                               sessions=(DAY,))
    client, keeper = fixture()
    client.rows["market_day_build_fence_v1"][0]["stage_hash"] = "c" * 64
    with pytest.raises(RuntimeError, match="final fence"):
        audit_attested_market_day_certificate(client, keeper, BUILD,
                                               sessions=(DAY,))


def test_cold_read_rejects_late_duplicate_and_misplaced_part() -> None:
    client, keeper = fixture()
    client.rows["market_day_stage_certificate_v1"].append(
        deepcopy(client.rows["market_day_stage_certificate_v1"][0]))
    with pytest.raises(RuntimeError, match="inventory"):
        audit_attested_market_day_certificate(client, keeper, BUILD,
                                               sessions=(DAY,))
    client, keeper = fixture()
    client.disk = "default"
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        audit_attested_market_day_certificate(client, keeper, BUILD,
                                               sessions=(DAY,))


@pytest.mark.parametrize("fault,match", [
    ("policy", "SSD-only"), ("layout", "layout differs"),
    ("columns", "columns differ"), ("parts", "outside live_market_ssd"),
])
def test_source_plan_schema_and_part_drift_fail_closed(fault, match) -> None:
    import json
    class DriftReader(FakeClickHouse):
        def execute(self, sql):
            if fault == "policy" and "FROM system.storage_policies" in sql:
                return json.dumps({"disks": ["default"]})
            if fault == "layout" and "FROM system.tables" in sql and "sorting_key" in sql:
                rows = [json.loads(line) for line in super().execute(sql).splitlines()]
                rows[0]["partition_key"] = "tuple()"
                return "\n".join(json.dumps(row) for row in rows)
            if fault == "columns" and "FROM system.columns" in sql:
                rows = [json.loads(line) for line in super().execute(sql).splitlines()]
                next(row for row in rows if row["name"] == "source_plan_hash")["type"] = "String"
                return "\n".join(json.dumps(row) for row in rows)
            if fault == "parts" and "FROM system.parts" in sql and "disk_name!='live_market_ssd'" in sql:
                return json.dumps({"table": "market_day_source_plan_v1",
                                   "disk_name": "default"})
            return super().execute(sql)
    source, keeper = fixture()
    client = DriftReader()
    client.rows = deepcopy(source.rows)
    with pytest.raises(RuntimeError, match=match):
        audit_attested_market_day_certificate(client, keeper, BUILD,
                                               sessions=(DAY,))


def test_zero_row_market_products_are_checked_but_source_gate_stays_closed() -> None:
    class ProductReader(FakeClickHouse):
        product_disk = "live_market_ssd"

        def execute(self, sql):
            if "FROM system.tables" in sql and "'bars_v1'" in sql:
                return "\n".join(json.dumps(dict(
                    name=name, storage_policy="live_market_ssd")) for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1"))
            if "FROM system.parts" in sql and "'bars_v1'" in sql:
                return json.dumps(dict(
                    table="bars_v1", disk_name=self.product_disk))
            if any(f"FROM arte.{name} " in sql for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1")):
                return ""  # Explicit zero-row stage certificates authorize absence.
            return super().execute(sql)

    source, keeper = fixture()
    client = ProductReader()
    client.rows = deepcopy(source.rows)
    audit = audit_attested_market_day_certificate(client, keeper, BUILD,
                                                   sessions=(DAY,))
    checked = verify_attested_market_products(
        client, audit, execution_interval="100ms",
        required_resolutions_ms=(100,))
    assert checked.market_products_verified
    assert checked.source_authority_verified  # V3 proof includes producer source parity.
    assert not checked.fixed_backtest_ready
    client.product_disk = "default"
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        verify_attested_market_products(client, audit,
            execution_interval="100ms", required_resolutions_ms=(100,))


def test_cold_source_replays_select_only_producer_plan_not_disk(monkeypatch) -> None:
    from scripts import build_market_day
    client, keeper = fixture()
    audit = audit_attested_market_day_certificate(client, keeper, BUILD,
                                                   sessions=(DAY,))
    calls = []
    def replay(source_client, args):
        assert source_client is client
        calls.append(args)
        return deepcopy(audit.source_plan)
    monkeypatch.setattr(build_market_day, "source_plan", replay)
    checked = replay_canonical_source_plan_parity(client, audit)
    assert checked.source_authority_verified
    assert not checked.fixed_backtest_ready  # Active cutover remains disabled.
    assert len(calls) == 1 and calls[0].symbols == ()
    def changed(_client, _args):
        plan = deepcopy(audit.source_plan)
        plan["rules"][0]["modifier_int"] = 12
        return plan
    monkeypatch.setattr(build_market_day, "source_plan", changed)
    with pytest.raises(RuntimeError, match="differs before publication"):
        replay_canonical_source_plan_parity(client, audit)


def test_cold_constructor_uses_attested_source_parity_and_matches_ledger_token() -> None:
    from src.backend.backtest_market_data import _stable_hash

    class ProductReader(FakeClickHouse):
        def execute(self, sql):
            if "FROM system.tables" in sql and "'bars_v1'" in sql:
                return "\n".join(json.dumps(dict(
                    name=name, storage_policy="live_market_ssd")) for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1"))
            if "FROM system.parts" in sql and "'bars_v1'" in sql:
                return ""
            if any(f"FROM arte.{name} " in sql for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1")):
                return ""  # All three attested output_rows are zero.
            return super().execute(sql)

    source, keeper = fixture()
    client = ProductReader()
    client.rows = deepcopy(source.rows)
    audit = audit_attested_market_day_certificate(client, keeper, BUILD,
                                                   sessions=(DAY,))
    assert audit.source_authority_verified
    plan = certified_market_day_plan_from_cold_audit(client, audit,
        sessions=(DAY,), tickers=(), configuration={})
    assert plan.sessions == (DAY,)
    assert plan.tickers == ("TEST",)
    assert len(plan.units) == 3
    assert plan.token == _stable_hash({
        "build_id": BUILD, "definition_hash": audit.certificate.definition_hash,
        "sessions": (DAY,), "tickers": ("TEST",), "resolutions": (100, 1000),
        "units": [[unit.build_id, unit.session_date, unit.ticker, unit.stage,
                   unit.attempt_id, unit.source_hash, unit.output_rows,
                   unit.output_hash] for unit in plan.units]})
    assert not audit.fixed_backtest_ready
    with pytest.raises(ValueError, match="outside attested population"):
        certified_market_day_plan_from_cold_audit(client, audit,
            sessions=(DAY,), tickers=("OTHER",), configuration={})


def test_cold_plan_entrypoint_never_replays_canonical_source(
        monkeypatch) -> None:
    from scripts import build_market_day

    class ProductReader(FakeClickHouse):
        product_reads = 0

        def execute(self, sql):
            if "FROM system.tables" in sql and "'bars_v1'" in sql:
                return "\n".join(json.dumps(dict(
                    name=name, storage_policy="live_market_ssd")) for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1"))
            if "FROM system.parts" in sql and "'bars_v1'" in sql:
                return ""
            if any(f"FROM arte.{name} " in sql for name in (
                    "bars_v1", "indicators_v1", "liquidity_100ms_v1")):
                self.product_reads += 1
                return ""
            return super().execute(sql)

    source, keeper = fixture()
    client = ProductReader()
    client.rows = deepcopy(source.rows)
    def forbidden_replay(_source_client, _args):
        raise AssertionError("Fixed Backtest must not query canonical events")
    monkeypatch.setattr(build_market_day, "source_plan", forbidden_replay)
    result = cold_certified_market_day_plan(client, keeper, BUILD,
        sessions=(DAY,), tickers=(), configuration={})
    assert result.tickers == ("TEST",)
    # Each selected product is hash-checked once; the constructor must not
    # rescan the entire certificate and then repeat the selected scope.
    assert client.product_reads == 3


def test_catalogue_discovery_requires_one_compatible_attested_build(monkeypatch) -> None:
    other = "b" * 64
    class Catalogue:
        def __init__(self, ids):
            self.ids = ids
        def execute(self, sql):
            assert sql.startswith("SELECT build_id FROM arte.market_day_build_fence_v1 ")
            selected = [value for value in self.ids if f"WHERE build_id='{value}'" in sql]
            return "\n".join(json.dumps({"build_id": value}) for value in
                             (selected if "WHERE" in sql else self.ids))
    calls = []
    def cold(_client, _keeper, build_id, **_kwargs):
        calls.append(build_id)
        return build_id
    monkeypatch.setattr(
        "src.trading_runtime.arte_market_day_cold_preflight.cold_certified_market_day_plan",
        cold)
    args = dict(sessions=(DAY,), tickers=(), configuration={})
    assert discover_cold_certified_market_day_plan(
        Catalogue([BUILD]), object(), **args) == BUILD
    with pytest.raises(RuntimeError, match="No arte market-day certificate"):
        discover_cold_certified_market_day_plan(Catalogue([]), object(), **args)
    with pytest.raises(RuntimeError, match="Multiple compatible"):
        discover_cold_certified_market_day_plan(
            Catalogue([BUILD, other]), object(), **args)
    assert discover_cold_certified_market_day_plan(
        Catalogue([BUILD, other]), object(),
        **{**args, "configuration": {"market_day_build_id": BUILD}}) == BUILD
    with pytest.raises(RuntimeError, match="duplicate identities"):
        discover_cold_certified_market_day_plan(
            Catalogue([BUILD, BUILD]), object(), **args)
    assert calls == [BUILD, BUILD, other, BUILD]
