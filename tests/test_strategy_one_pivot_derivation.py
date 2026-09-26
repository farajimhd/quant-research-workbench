"""Producer uses persisted one-second bars without inventing missing bars."""
from datetime import date

import pytest

from pipelines.strategy_one.pivot_derivation import derive_pivot_intervals
from src.backend.backtest_market_data import (
    SESSION_OPEN_OFFSET_MS, market_day_boundary,
)


def source(index, price=100_000, *, valid=1):
    return {"ticker": "XYZ", "resolution_ms": 1_000,
            "bucket_index": SESSION_OPEN_OFFSET_MS // 1_000 + index,
            "price_valid": valid, "extremes_valid": valid,
            "open_int": price, "high_int": price + 100,
            "low_int": price - 100, "close_int": price}


def test_completed_source_seconds_are_accepted_without_events_or_writes():
    prices = [100_000, 99_000, 98_000, 99_000, 100_000, 101_000,
              102_000, 101_000] * 12
    intervals = derive_pivot_intervals(
        (source(index, price) for index, price in enumerate(prices)),
        session_date="2026-08-18", ticker="XYZ")
    assert intervals
    assert all(item.confirmed_at_us <= int(market_day_boundary(
        date(2026, 8, 18), item.valid_from_boundary_ms).timestamp() * 1_000_000)
        for item in intervals)


def test_empty_bucket_is_not_made_into_a_candle():
    assert derive_pivot_intervals(
        (source(0, valid=0), source(2)),
        session_date="2026-08-18", ticker="XYZ") == ()


def test_duplicate_or_foreign_source_fails_closed():
    with pytest.raises(ValueError, match="order"):
        derive_pivot_intervals(
            (source(0), source(0)), session_date="2026-08-18", ticker="XYZ")
    with pytest.raises(ValueError, match="identity"):
        derive_pivot_intervals(
            ({**source(0), "ticker": "ABC"},),
            session_date="2026-08-18", ticker="XYZ")
