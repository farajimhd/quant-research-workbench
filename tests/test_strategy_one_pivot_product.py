"""Normalize causal detector snapshots without repeated pivot copies."""
import pytest

from src.market_engine.structural_detector import StructuralDetector, VERSION
from src.trading_runtime.early_squeeze_momentum import confirmed_pivots
from src.trading_runtime.strategy_one_pivot_product import (
    PivotInterval, PivotIntervalBuilder, interval_content_hash,
    snapshot_confirmed_pivots,
)


def row(end, swings=()):
    return {"contract": VERSION, "effective_at": end,
            "local_swings": list(swings), "confirmed_swings": []}


def pivot():
    return {"side": "resistance", "state": "active", "price": 10.125,
            "pivot_at": 1.0, "confirmed_at": 2.0}


def test_repeated_snapshot_becomes_one_open_interval():
    builder = PivotIntervalBuilder()
    builder.observe(1_000, row(1.0))
    builder.observe(2_000, row(2.0, (pivot(),)))
    builder.observe(3_000, row(3.0, (pivot(),)))
    interval, = builder.finish()
    assert (interval.side, interval.price_int,
            interval.valid_from_boundary_ms, interval.valid_to_boundary_ms) == (
                "high", 101_250, 2_000, None)


def test_disappearance_and_gap_close_visibility_without_fabrication():
    builder = PivotIntervalBuilder()
    builder.observe(2_000, row(2.0, (pivot(),)))
    builder.observe(3_000, row(3.0))
    builder.observe(4_000, row(4.0, (pivot(),)))
    builder.observe(6_000, row(6.0, (pivot(),)))
    intervals = builder.finish()
    assert [(item.valid_from_boundary_ms, item.valid_to_boundary_ms)
            for item in intervals] == [(2_000, 3_000), (4_000, 5_000),
                                      (6_000, None)]


def test_duplicate_local_and_new_confirmation_is_one_identity():
    evidence = row(2.0, (pivot(),))
    evidence["confirmed_swings"] = [pivot()]
    assert len(snapshot_confirmed_pivots(evidence)) == 1


def test_unknown_contract_or_future_pivot_fails_closed():
    with pytest.raises(ValueError, match="contract"):
        snapshot_confirmed_pivots({**row(2.0), "contract": "unknown"})
    assert not snapshot_confirmed_pivots(row(1.5, (pivot(),)))
    builder = PivotIntervalBuilder()
    with pytest.raises(ValueError, match="no completed"):
        builder.finish()


def test_real_detector_snapshots_match_legacy_confirmed_pivot_visibility():
    engine = StructuralDetector()
    prices = [10., 9.9, 9.8, 9.9, 10., 10.1, 10.2, 10.1] * 12
    matched = 0
    for index, price in enumerate(prices):
        now = 1_000 + index
        evidence = engine.observe({
            "time": now, "end": now + 1, "open": price - .01,
            "close": price, "low": price - .02, "high": price + .02,
        })
        actual = snapshot_confirmed_pivots(evidence)
        expected = frozenset((side, round(pivot["price"] * 10_000),
                              round(pivot["pivot_at"] * 1_000_000),
                              round(pivot["confirmed_at"] * 1_000_000))
                             for side in ("high", "low")
                             for pivot in confirmed_pivots(evidence, now + 1, side))
        assert actual == expected
        matched += len(actual)
    assert matched > 0


def test_content_hash_rejects_duplicate_or_overlapping_pivot_intervals():
    first = PivotInterval("high", 100_000, 1_000_000, 2_000_000,
                          2_000, 4_000)
    later = PivotInterval("high", 100_000, 1_000_000, 2_000_000,
                          4_000, None)
    assert len(interval_content_hash((first, later))) == 64
    with pytest.raises(ValueError, match="overlapping"):
        interval_content_hash((first, PivotInterval(
            "high", 100_000, 1_000_000, 2_000_000, 3_000, None)))
    with pytest.raises(ValueError, match="duplicate"):
        interval_content_hash((first, first))


def test_canonical_same_boundary_side_order_is_lexical_not_enum_numeric():
    builder = PivotIntervalBuilder()
    low = {**pivot(), "side": "support", "price": 9.5}
    builder.observe(2_000, row(2.0, (low, pivot())))
    intervals = builder.finish()
    assert [item.side for item in intervals] == ["high", "low"]
    assert len(interval_content_hash(intervals)) == 64
