"""Backtest SELECTs only exact certified Strategy 1 candidate attempts."""
import json
from hashlib import sha256

import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.backend.backtest_strategy_one_candidate_contract import candidate_content_hash
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.fixed_bar_signal import first_squeeze_sql
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST


DAY = "2026-08-18"
BUILD = "build"
ATTEMPT = "00000000-0000-0000-0000-000000000001"
DERIVED = "00000000-0000-0000-0000-000000000002"
EMPTY_HASH = candidate_content_hash(())
THROUGH = 57_600_000
CHILD = dict(
    source_build_id=BUILD, session_date=DAY, ticker="ABCD",
    derivation_attempt_id=DERIVED, boundary_ms=30_000,
    source_row_index=10, episode_start_ms=29_900,
    macd_1s_boundary_ms=30_000, macd_5s_boundary_ms=30_000,
    macd_10s_boundary_ms=30_000, macd_30s_boundary_ms=30_000,
    stop_30s_boundary_ms=30_000, stop_low_int=150_000,
)


def _market():
    units = tuple(MarketDayUnit(BUILD, DAY, ticker, stage, ATTEMPT,
                                "source", 100, "hash")
                  for ticker in ("ABCD", "EFGH")
                  for stage in ("bars", "technical", "broker_100ms"))
    return CertifiedMarketDayPlan(ExecutionInterval.fixed(100), BUILD,
                                  "definition", (DAY,), ("ABCD", "EFGH"),
                                  units, (100, 1000, 5000, 10000, 30000),
                                  "market-token")


SCAN = sha256(first_squeeze_sql(_market(), through_boundary_ms=THROUGH).encode()).hexdigest()


class Reader:
    def __init__(self, *, missing=False, changed=False, misplaced=False):
        self.queries = []
        self.missing, self.changed, self.misplaced = missing, changed, misplaced

    def execute(self, query):
        self.queries.append(query)
        if "FROM system.tables" in query:
            return "\n".join(json.dumps({"name": name,
                "storage_policy": "live_market_ssd"}) for name in (
                "strategy_one_candidate_v1",
                "strategy_one_candidate_coverage_v1"))
        if "FROM system.parts" in query:
            return (json.dumps({"table": "strategy_one_candidate_v1",
                                "disk_name": "default"}) if self.misplaced else "")
        if "FROM arte.strategy_one_candidate_coverage_v1" in query:
            if self.missing:
                return ""
            return "\n".join(json.dumps(dict(
                session_date=DAY, ticker=ticker,
                derivation_attempt_text=DERIVED,
                bars_attempt_text=ATTEMPT,
                technical_attempt_text=ATTEMPT,
                liquidity_attempt_text=ATTEMPT,
                candidate_rule_digest=RULE_DIGEST,
                scan_query_sha256=SCAN,
                candidate_count=1 if ticker == "ABCD" else 0,
                content_hash=(candidate_content_hash((CHILD,))
                              if ticker == "ABCD" else EMPTY_HASH)))
                for ticker in ("ABCD", "EFGH"))
        if "FROM arte.strategy_one_candidate_v1" in query:
            row = {key: value for key, value in CHILD.items()
                   if key not in {"source_build_id", "derivation_attempt_id"}}
            row["derivation_attempt_text"] = DERIVED
            if self.changed:
                row["stop_low_int"] += 1
            return json.dumps(row)
        raise AssertionError(query)


def test_candidate_reader_seals_positive_and_empty_ticker():
    reader = Reader()
    result = certify_candidate_plan(_market(), candidate_rule_digest=RULE_DIGEST,
                                    through_boundary_ms=THROUGH,
                                    client=reader)
    assert len(result.coverage) == 2
    assert len(result.prepared) == 1
    assert result.prepared[0].ticker == "ABCD"
    assert result.prepared[0].stop_low_int.tolist() == [150_000]
    assert len(result.token) == 64
    assert all(query.startswith("SELECT") and "INSERT" not in query
               for query in reader.queries)
    coverage_query = next(query for query in reader.queries
                          if "FROM arte.strategy_one_candidate_coverage_v1" in query)
    assert "(toDate('2026-08-18'),'ABCD'),(toDate('2026-08-18'),'EFGH')" in coverage_query


def test_missing_tampered_or_misplaced_candidates_fail_closed():
    with pytest.raises(RuntimeError, match="missing or duplicate"):
        certify_candidate_plan(_market(), candidate_rule_digest=RULE_DIGEST,
                               through_boundary_ms=THROUGH,
                               client=Reader(missing=True))
    with pytest.raises(RuntimeError, match="differ from coverage"):
        certify_candidate_plan(_market(), candidate_rule_digest=RULE_DIGEST,
                               through_boundary_ms=THROUGH,
                               client=Reader(changed=True))
    with pytest.raises(RuntimeError, match="outside SSD"):
        certify_candidate_plan(_market(), candidate_rule_digest=RULE_DIGEST,
                               through_boundary_ms=THROUGH,
                               client=Reader(misplaced=True))
    with pytest.raises(RuntimeError, match="pinned authority"):
        certify_candidate_plan(_market(), candidate_rule_digest=RULE_DIGEST,
                               through_boundary_ms=30_000,
                               client=Reader())
