"""A certified pivot is visible only in its completed validity interval."""
import pytest

from src.backend.backtest_market_data import market_day_boundary
from src.trading_runtime.strategy_one_pivot_product import PivotInterval
from src.trading_runtime.strategy_one_pivot_timeline import PivotTimeline


DAY = "2026-08-18"
ORIGIN = round(market_day_boundary(DAY, 0).timestamp() * 1_000_000)


def interval(side, price, start, end=None):
    return PivotInterval(side, price, ORIGIN + 1_000_000,
                         ORIGIN + 2_000_000, start, end)


def test_completed_visibility_and_exclusive_expiry_at_100ms_boundaries():
    timeline = PivotTimeline(session_date=DAY, intervals=(
        interval("high", 101_000, 2_000, 3_000),
        interval("low", 99_000, 2_000, None),
    ))
    assert timeline.at(1_900) == ()
    assert [item.side for item in timeline.at(2_000)] == ["high", "low"]
    assert len(timeline.at(2_900)) == 2
    assert [item.side for item in timeline.at(3_000)] == ["low"]


def test_future_or_repeated_clock_fails_closed():
    with pytest.raises(ValueError, match="noncausal"):
        PivotTimeline(session_date=DAY, intervals=(PivotInterval(
            "high", 101_000, ORIGIN + 1_000_000,
            ORIGIN + 3_000_000, 2_000, None),))
    timeline = PivotTimeline(session_date=DAY, intervals=())
    assert timeline.at(100) == ()
    with pytest.raises(ValueError, match="increasing"):
        timeline.at(100)


def test_sparse_jump_never_resurrects_an_expired_interval():
    timeline = PivotTimeline(session_date=DAY, intervals=(
        interval("high", 101_000, 2_000, 3_000),
        interval("high", 101_000, 4_000, None)))
    visible = timeline.at(5_000)
    assert len(visible) == 1 and visible[0].side == "high"
