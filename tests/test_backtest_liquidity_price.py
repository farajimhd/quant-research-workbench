"""Fixed Backtest may certify eligible prices, but never build them."""
import json
import re
from threading import Barrier, Lock

import pytest

from pipelines.market_sip.events.liquidity_execution_price_producer import _digest
from src.trading_runtime.eligible_price_contract import (
    legacy_summary_digest, volumes_match,
)
from src.backend.backtest_liquidity_price import (
    PriceLevelPlan, PriceLevelUnit, certify_price_level_plan,
)
from src.backend import backtest_liquidity_price as price_subject
from src.backend import backtest_market_plan_cache as plan_cache
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
    iter_market_day_rows, market_day_source_sqls,
)


def test_volume_reduction_order_tolerance_preserves_material_mismatch():
    assert volumes_match(31676474.654285185, 31676474.654286593)
    assert not volumes_match(31676474.654285185, 31676474.66)
    assert not volumes_match(float("nan"), 1.0)


SOURCE = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"
SUMMARY = dict(row_count=2, unique_keys=2, eligible_bucket_count=1,
               total_execution_volume=40.0, row_hash="123")


def _plan():
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), "build", "definition",
        ("2026-08-18",), ("ABCD",),
        (MarketDayUnit("build", "2026-08-18", "ABCD", "broker_100ms",
                       SOURCE, "source", 10, "hash"),), (100,), "market-token")


class Reader:
    def __init__(self, *, coverage=True, misplaced=False, tampered=False,
                 legacy=False, integer_legacy=False):
        self.queries = []
        self.coverage = coverage
        self.misplaced = misplaced
        self.tampered = tampered
        self.legacy = legacy
        self.integer_legacy = integer_legacy

    def execute(self, query):
        self.queries.append(query)
        if "FROM system.tables" in query:
            return "\n".join(json.dumps({"name": name,
                "storage_policy": "live_market_ssd"}) for name in (
                "liquidity_execution_price_100ms_v1",
                "liquidity_execution_price_coverage_v1"))
        if "FROM system.parts" in query:
            return json.dumps({"table": "liquidity_execution_price_100ms_v1",
                               "disk_name": "default"}) if self.misplaced else ""
        if "FROM arte.liquidity_execution_price_coverage_v1" in query:
            if not self.coverage:
                return ""
            return json.dumps(dict(session_date="2026-08-18", ticker="ABCD",
                source_attempt_text=SOURCE, derivation_attempt_text=DERIVED,
                price_row_count=2, eligible_bucket_count=1,
                total_execution_volume=(40 if self.integer_legacy else
                                        40.00000001 if self.legacy else 40.0),
                content_hash=(legacy_summary_digest(
                    SUMMARY, published_volume=(40 if self.integer_legacy else
                                               40.00000001))
                    if self.legacy or self.integer_legacy else _digest(SUMMARY))))
        if "FROM arte.liquidity_execution_price_100ms_v1" in query:
            return json.dumps(dict(session_date="2026-08-18", ticker="ABCD",
                source_attempt_text=SOURCE, derivation_attempt_text=DERIVED,
                **{**SUMMARY, "row_hash": "wrong" if self.tampered else "123"}))
        raise AssertionError(query)


def test_certified_child_plan_pins_source_attempt_and_read_only_hash():
    reader = Reader()
    result = certify_price_level_plan(_plan(), reader)
    assert len(result.units) == 1
    assert result.units[0].source_attempt_id == SOURCE
    assert result.units[0].derivation_attempt_id == DERIVED
    assert len(result.token) == 64
    assert all(query.startswith("SELECT") and "INSERT" not in query
               for query in reader.queries)
    assert all("AS source_attempt_id" not in query and
               "AS derivation_attempt_id" not in query
               for query in reader.queries)


def test_price_certification_uses_bounded_independent_readers(monkeypatch):
    monkeypatch.setattr(price_subject, "_CERTIFICATION_BATCH_SIZE", 1)
    tickers = ("ABCD", "EFGH", "IJKL", "MNOP", "WXYZ")
    market = CertifiedMarketDayPlan(
        ExecutionInterval.fixed(100), "build", "definition",
        ("2026-08-18",), tickers,
        tuple(MarketDayUnit(
            "build", "2026-08-18", ticker, "broker_100ms",
            SOURCE, "source", 10, "hash") for ticker in tickers),
        (100,), "five-ticker-market-token")
    concurrent = Barrier(4, timeout=5)
    owned = []
    factory_lock = Lock()

    class OwnedReader(Reader):
        def __init__(self):
            super().__init__()
            self.closed = False
            self.first_coverage = True

        def execute(self, query):
            if ("FROM arte.liquidity_execution_price_coverage_v1" in query
                    and self.first_coverage):
                self.first_coverage = False
                concurrent.wait()
            ticker = re.search(r"'([A-Z]{4})'", query).group(1)
            return super().execute(query.replace(ticker, "ABCD")).replace(
                "ABCD", ticker)

        def close(self):
            self.closed = True

    def factory():
        reader = OwnedReader()
        with factory_lock:
            owned.append(reader)
        return reader

    plan = certify_price_level_plan(
        market, Reader(), read_client_factory=factory)
    assert tuple(unit.ticker for unit in plan.units) == tickers
    assert len(owned) == 4 and all(reader.closed for reader in owned)
    assert sorted(sum("FROM arte.liquidity_execution_price_coverage_v1" in query
                      for query in reader.queries) for reader in owned) == [1, 1, 1, 2]
    assert all(query.startswith("SELECT") and "INSERT" not in query
               for reader in owned for query in reader.queries)


def test_missing_or_misplaced_child_blocks_preflight():
    with pytest.raises(RuntimeError, match="coverage"):
        certify_price_level_plan(_plan(), Reader(coverage=False))
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        certify_price_level_plan(_plan(), Reader(misplaced=True))


def test_tampered_child_blocks_preflight():
    with pytest.raises(RuntimeError, match="differ from published coverage"):
        certify_price_level_plan(_plan(), Reader(tampered=True))


def test_legacy_coverage_float_sum_drift_is_not_a_false_blocker():
    assert certify_price_level_plan(_plan(), Reader(legacy=True)).units[0].price_row_count == 2


def test_legacy_integer_volume_literal_is_preserved_for_digest():
    unit = certify_price_level_plan(_plan(), Reader(integer_legacy=True)).units[0]
    assert unit.published_volume_text == "40"


def test_price_certificate_cache_reuses_only_unchanged_clickhouse_parts(monkeypatch):
    from research.mlops import clickhouse

    class CacheReader(Reader):
        part_name = "price-part-1"
        disk_name = "live_market_ssd"

        def execute(self, query):
            if "FROM system.tables" in query and "uuid" in query:
                return "\n".join(json.dumps(dict(
                    name=name, uuid=f"uuid-{name}", storage_policy="live_market_ssd",
                    metadata_modification_time="2026-09-25 00:00:00"))
                    for name in plan_cache._PRICE_NAMES)
            if "FROM system.columns" in query:
                return "\n".join(json.dumps(dict(
                    table=name, name="source_build_id", type="String", position=1))
                    for name in plan_cache._PRICE_NAMES)
            if "FROM system.parts" in query and "hash_of_all_files" in query:
                return json.dumps(dict(
                    table=plan_cache._PRICE_NAMES[0], name=self.part_name,
                    disk_name=self.disk_name, rows=2, bytes_on_disk=100,
                    hash_of_all_files="a" * 32))
            return super().execute(query)

    monkeypatch.setattr(clickhouse, "ClickHouseHttpClient", CacheReader)
    monkeypatch.setattr(price_subject, "PRICE_PLAN_CACHE",
                        plan_cache.FingerprintPlanCache())
    reader = CacheReader()
    first = certify_price_level_plan(_plan(), reader)
    assert certify_price_level_plan(_plan(), reader) is first
    assert sum("FROM arte.liquidity_execution_price_100ms_v1" in query
               for query in reader.queries) == 1
    reader.part_name = "price-part-2"
    assert certify_price_level_plan(_plan(), reader) is not first
    assert sum("FROM arte.liquidity_execution_price_100ms_v1" in query
               for query in reader.queries) == 2
    reader.disk_name = "default"
    with pytest.raises(RuntimeError, match="outside SSD"):
        certify_price_level_plan(_plan(), reader)


def test_pinned_price_levels_join_and_decode_without_market_writes():
    base = _plan()
    units = base.units + tuple(MarketDayUnit(
        "build", "2026-08-18", "ABCD", stage, SOURCE, "source", 10, "hash")
        for stage in ("bars", "technical"))
    market = CertifiedMarketDayPlan(base.execution_interval, base.build_id,
        base.definition_hash, base.sessions, base.tickers, units,
        base.required_resolutions_ms, base.token)
    child = PriceLevelPlan("build", (PriceLevelUnit(
        "2026-08-18", "ABCD", SOURCE, DERIVED, 2, 1, 40.0,
        _digest(SUMMARY)),), "child-token")
    query, = market_day_source_sqls(market, price_plan=child,
                                    through_boundary_ms=100)
    assert "arte.liquidity_execution_price_100ms_v1" in query
    assert "arraySort(x->x.1,groupArray((price_int,execution_volume)))" in query
    assert "derivation_attempt_id" in query
    assert "bucket_index<144001" in query
    assert "market_sip_compact" not in query
    class Source:
        def iter_json_each_row(self, sql):
            assert sql == query
            yield dict(session_date="2026-08-18", ticker="ABCD",
                       resolution_ms=100, boundary_ms=100,
                       execution_price_levels=[[98000, 5], [99500, 35]])
    row, = iter_market_day_rows(market, client=Source(),
                                price_plan=child, through_boundary_ms=100)
    assert row["execution_price_levels"] == (
        {"price_int": 98000, "volume": 5.0},
        {"price_int": 99500, "volume": 35.0})
