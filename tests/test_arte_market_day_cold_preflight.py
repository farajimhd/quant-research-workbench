from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate, replay_canonical_source_plan_parity,
    verify_attested_market_products,
)
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperAuthority
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
    assert not checked.source_authority_verified
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
    assert not checked.fixed_backtest_ready  # Source storage remains unproved.
    assert len(calls) == 1 and calls[0].symbols == ()
    def changed(_client, _args):
        plan = deepcopy(audit.source_plan)
        plan["rules"][0]["modifier_int"] = 12
        return plan
    monkeypatch.setattr(build_market_day, "source_plan", changed)
    with pytest.raises(RuntimeError, match="differs from attested"):
        replay_canonical_source_plan_parity(client, audit)
