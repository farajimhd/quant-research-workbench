"""Causal compact derivative of Strategy 1 V7 completed-second geometry."""
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.market_engine.streaming_level_book import VERSION
from src.trading_runtime.strategy_one_v7_intervals import (
    V7IntervalProjector, levels_at,
)


def level(identity="R1", *, lower=10.0, upper=10.2, role="resistance",
          confirmed=1_800_000_000_000):
    return {"unified_level_id": identity, "lower": lower, "upper": upper,
            "role": role, "transition_from": None,
            "confirmed_at_ms": confirmed, "historical": True,
            "book_version": VERSION}


def test_intervals_reuse_unchanged_geometry_and_require_fresh_clock():
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0, levels=[level()],
                      valid_completed_second=False)
    projector.observe(boundary_ms=1_000, levels=[level()],
                      valid_completed_second=True)
    projector.observe(boundary_ms=2_000, levels=[level()],
                      valid_completed_second=True)
    projector.observe(boundary_ms=3_000, levels=[level(upper=10.3)],
                      valid_completed_second=True)
    projector.observe(boundary_ms=4_000, levels=(),
                      valid_completed_second=False)
    clocks, intervals = projector.finish()
    assert clocks == (1_000, 2_000, 3_000)
    assert [(row.valid_from_ms, row.valid_to_ms, row.upper)
            for row in intervals] == [(0, 3_000, 10.2),
                                     (3_000, 57_600_001, 10.3)]
    assert levels_at(boundary_ms=2_900, valid_seconds=clocks,
                     intervals=intervals)[0]["upper"] == 10.2
    assert levels_at(boundary_ms=3_000, valid_seconds=clocks,
                     intervals=intervals)[0]["upper"] == 10.3
    assert levels_at(boundary_ms=4_000, valid_seconds=clocks,
                     intervals=intervals)[0]["upper"] == 10.3
    assert levels_at(boundary_ms=4_100, valid_seconds=clocks,
                     intervals=intervals) == ()


def test_no_future_projection_or_invalid_second_geometry():
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0, levels=[level()],
                      valid_completed_second=False)
    with pytest.raises(ValueError, match="Invalid V7 second"):
        projector.observe(boundary_ms=1_000, levels=[level(upper=10.3)],
                          valid_completed_second=False)
    projector.observe(boundary_ms=1_000, levels=(),
                      valid_completed_second=False)
    projector.observe(boundary_ms=2_000, levels=[level(upper=10.3)],
                      valid_completed_second=True)
    clocks, intervals = projector.finish()
    assert levels_at(boundary_ms=1_900, valid_seconds=clocks,
                     intervals=intervals) == ()
    assert levels_at(boundary_ms=2_000, valid_seconds=clocks,
                     intervals=intervals)[0]["upper"] == 10.3
    with pytest.raises(ValueError, match="overlapping"):
        levels_at(boundary_ms=2_000, valid_seconds=clocks,
                  intervals=(*intervals, replace(intervals[-1], lower=9.0)))


def test_malformed_geometry_and_duplicate_clocks_fail_closed():
    projector = V7IntervalProjector()
    with pytest.raises(ValueError, match="scalar level geometry"):
        projector.observe(boundary_ms=0, levels=[level(lower=0.0)],
                          valid_completed_second=False)
    projector.observe(boundary_ms=0, levels=[level()],
                      valid_completed_second=False)
    with pytest.raises(ValueError, match="ordered completed-second"):
        projector.observe(boundary_ms=0, levels=(),
                          valid_completed_second=False)
    with pytest.raises(ValueError, match="ordered"):
        levels_at(boundary_ms=1_000, valid_seconds=(1_000, 1_000),
                  intervals=())


def test_projection_retains_engine_order_even_when_ids_sort_differently():
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0,
                      levels=[level("Z"), level("A", lower=11.0, upper=11.2)],
                      valid_completed_second=False)
    clocks, intervals = projector.finish()
    assert [row["unified_level_id"] for row in levels_at(
        boundary_ms=0, valid_seconds=clocks, intervals=intervals)] == ["Z", "A"]


def test_interval_projection_matches_real_fixed_v7_seed_and_completed_bar():
    from src.backend.fixed_v7_stream import FixedV7Stream
    from src.backend.structural_v7_seed import load_seed
    from tests.test_structural_v7_seed import Client

    prior = load_seed(Client(), ticker="TEST", session=date(2026, 8, 18))
    stream = FixedV7Stream(prior, ticker="TEST", session=date(2026, 8, 18))
    ny = ZoneInfo("America/New_York")
    opening = datetime(2026, 8, 18, 4, 0, tzinfo=ny)
    projector = V7IntervalProjector()
    projector.observe(boundary_ms=0, levels=stream.strategy_one_levels(
        as_of=opening, seed_policy=prior["input_policy"]),
        valid_completed_second=False)
    at = datetime(2026, 8, 18, 4, 5, 1, tzinfo=ny)
    stream.update_second({
        "resolution_ms": 1_000, "price_valid": 1, "extremes_valid": 1,
        "open_int": 100_000, "high_int": 100_100, "low_int": 99_900,
        "close_int": 100_050, "volume": 100,
    }, at=at)
    projector.observe(boundary_ms=301_000, levels=stream.strategy_one_levels(
        as_of=at, seed_policy=prior["input_policy"]),
        valid_completed_second=True)
    clocks, intervals = projector.finish()
    expected = stream.strategy_one_levels(
        as_of=at, seed_policy=prior["input_policy"])
    actual = levels_at(boundary_ms=301_000, valid_seconds=clocks,
                       intervals=intervals)
    assert [(row["unified_level_id"], row["lower"], row["upper"], row["role"])
            for row in actual] == [
                (row["unified_level_id"], row["lower"], row["upper"], row["role"])
                for row in expected]
