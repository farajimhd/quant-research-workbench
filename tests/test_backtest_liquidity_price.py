"""Fixed Backtest may certify eligible prices, but never build them."""
import json

import pytest

from pipelines.market_sip.events.liquidity_execution_price_producer import _digest
from src.backend.backtest_liquidity_price import certify_price_level_plan
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)


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
    def __init__(self, *, coverage=True, misplaced=False, tampered=False):
        self.queries = []
        self.coverage = coverage
        self.misplaced = misplaced
        self.tampered = tampered

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
                source_attempt_id=SOURCE, derivation_attempt_id=DERIVED,
                price_row_count=2, eligible_bucket_count=1,
                total_execution_volume=40.0, content_hash=_digest(SUMMARY)))
        if "FROM arte.liquidity_execution_price_100ms_v1" in query:
            return json.dumps(dict(session_date="2026-08-18", ticker="ABCD",
                source_attempt_id=SOURCE, derivation_attempt_id=DERIVED,
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


def test_missing_or_misplaced_child_blocks_preflight():
    with pytest.raises(RuntimeError, match="coverage"):
        certify_price_level_plan(_plan(), Reader(coverage=False))
    with pytest.raises(RuntimeError, match="outside live_market_ssd"):
        certify_price_level_plan(_plan(), Reader(misplaced=True))


def test_tampered_child_blocks_preflight():
    with pytest.raises(RuntimeError, match="differ from published coverage"):
        certify_price_level_plan(_plan(), Reader(tampered=True))
