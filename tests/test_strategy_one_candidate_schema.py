"""Candidate sidecar is sparse, normalized, and separate from source products."""
from src.trading_runtime.strategy_one_candidate_schema import (
    CANDIDATE_TABLE, COVERAGE_TABLE, ddl,
)


def test_candidate_layout_is_typed_ssd_and_coverage_last():
    candidate, coverage = ddl()
    assert CANDIDATE_TABLE in candidate and COVERAGE_TABLE in coverage
    assert "storage_policy='live_market_ssd'" in candidate
    assert "storage_policy='live_market_ssd'" in coverage
    assert "PARTITION BY toYYYYMM(session_date)" in candidate
    assert "PARTITION BY toYYYYMM(session_date)" in coverage
    assert "source_row_index UInt32" in candidate
    assert "macd_30s_boundary_ms UInt32" in candidate
    assert "stop_low_int UInt64" in candidate
    assert "candidate_count UInt32" in coverage
    assert "bars_attempt_id UUID" in coverage
    assert "technical_attempt_id UUID" in coverage
    assert "liquidity_attempt_id UUID" in coverage
    assert all(forbidden not in (candidate + coverage).lower()
               for forbidden in ("json", "blob", "sqlite"))
    assert "arte.bars_v1" not in candidate + coverage
