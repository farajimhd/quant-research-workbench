"""Producer-only structural pivot derivation from pinned persisted 1s bars.

This module never reads SIP events or writes market tables. Its caller owns a
read-only certified ARTE bar stream and separately publishes the normalized
interval/coverage product. No Backtest or live execution path imports it.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Mapping

from src.backend.backtest_market_data import (
    SESSION_OPEN_OFFSET_MS, market_day_boundary,
)
from src.market_engine.structural_detector import StructuralDetector
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, PivotIntervalBuilder,
)


def derive_pivot_intervals(
    rows: Iterable[Mapping], *, session_date: str, ticker: str,
) -> tuple[PivotInterval, ...]:
    """Process complete price-bearing seconds once, never fabricate gaps.

    Empty persisted scheduler buckets do not reach the detector. A missing
    second resets the detector's local swing episode and closes active pivot
    visibility; it is not bridged by a later bar or a synthetic candle.
    """
    day = date.fromisoformat(session_date)
    detector = StructuralDetector()
    intervals = PivotIntervalBuilder()
    previous_bucket: int | None = None
    for source in rows:
        if not isinstance(source, Mapping):
            raise ValueError("Structural pivot source row is malformed")
        bucket = source.get("bucket_index")
        if (source.get("ticker") != ticker
                or source.get("resolution_ms") != 1_000
                or type(bucket) is not int
                or bucket < SESSION_OPEN_OFFSET_MS // 1_000
                or bucket >= (SESSION_OPEN_OFFSET_MS + 57_600_000) // 1_000
                or previous_bucket is not None and bucket <= previous_bucket):
            raise ValueError("Structural pivot source identity/order is invalid")
        previous_bucket = bucket
        if source.get("price_valid") != 1:
            continue
        if source.get("extremes_valid") != 1:
            raise ValueError("Price-bearing structural bar lacks valid extremes")
        prices = tuple(source.get(key) for key in (
            "open_int", "high_int", "low_int", "close_int"))
        if any(type(value) is not int or value <= 0 for value in prices):
            raise ValueError("Structural pivot source lacks integer OHLC")
        opened, high, low, close = prices
        if not low <= min(opened, close) <= max(opened, close) <= high:
            raise ValueError("Structural pivot source OHLC is inconsistent")
        boundary = (bucket + 1) * 1_000 - SESSION_OPEN_OFFSET_MS
        end = market_day_boundary(day, boundary)
        start = end - timedelta(seconds=1)
        # The shared detector sees only completed bar clocks and prices. Its
        # local pivot extractor is independent of V7/global-level projections.
        observation = detector.observe({
            "time": start.timestamp(), "end": end.timestamp(),
            "open": opened / 10_000, "high": high / 10_000,
            "low": low / 10_000, "close": close / 10_000,
        })
        intervals.observe(boundary, observation)
    return intervals.finish() if intervals.last_boundary_ms else ()
