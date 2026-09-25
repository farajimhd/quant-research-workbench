from __future__ import annotations

from copy import deepcopy

import pytest

from src.trading_runtime.arte_market_day_cold_preflight import (
    audit_attested_market_day_certificate,
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
