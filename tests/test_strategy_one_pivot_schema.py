"""The pivot product is normalized, SSD-pinned, and coverage-last."""
from src.trading_runtime.strategy_one_pivot_schema import (
    COVERAGE_TABLE, PIVOT_TABLE, PRODUCT_DIGEST, ddl,
)


def test_normalized_pivot_schema_has_no_snapshot_or_blob_columns():
    intervals, coverage = ddl()
    assert f"CREATE TABLE IF NOT EXISTS {PIVOT_TABLE}" in intervals
    assert f"CREATE TABLE IF NOT EXISTS {COVERAGE_TABLE}" in coverage
    assert "valid_to_boundary_ms Nullable(UInt32)" in intervals
    assert "bars_attempt_id UUID" in coverage
    assert "storage_policy='live_market_ssd'" in intervals
    assert "storage_policy='live_market_ssd'" in coverage
    assert len(PRODUCT_DIGEST) == 64
    assert all(term not in intervals.lower() + coverage.lower()
               for term in (" json", " blob", "checkpoint", "snapshot"))
