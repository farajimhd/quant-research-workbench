from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

from src.market_engine.volume_rank_rules import VolumeUniverseRule, select_volume_universe


def snapshot():
    now = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
    return pl.DataFrame({"listing_id": ["b", "a", "c", "d", "e"],
        "ticker": ["B", "A", "C", "D", "E"], "boundary_at": [now] * 5,
        "available_at": [now] * 5, "prior_close_available_at": [now - timedelta(days=1)] * 5,
        "adjusted_prior_close": [50., .8, .79, 50.01, 10.],
        "rolling_volume": [100., 100., 10000., 10000., 0.],
        "volume_window_seconds": [30] * 5})


def test_prior_close_gate_precedes_ranking_and_ties_use_listing_identity():
    actual = select_volume_universe(snapshot(), VolumeUniverseRule(30, 10, .8, 50))
    assert actual["listing_id"].to_list() == ["a", "b"]
    assert actual["rank"].to_list() == [1, 2]


def test_top_count_is_a_generic_rule_parameter():
    actual = select_volume_universe(snapshot(), VolumeUniverseRule(30, 1, .8, 50))
    assert actual["listing_id"].to_list() == ["a"]


def test_future_input_and_wrong_window_fail_closed():
    for bad in (snapshot().with_columns(pl.lit(60).alias("volume_window_seconds")),
                snapshot().with_columns(pl.col("available_at") + pl.duration(seconds=1))):
        with pytest.raises(ValueError, match="incompatible"):
            select_volume_universe(bad, VolumeUniverseRule(30, 10, .8, 50))


def test_ranking_is_independent_between_boundaries():
    first = snapshot()
    later = first.with_columns(pl.col("boundary_at") + pl.duration(seconds=1),
                               pl.col("available_at") + pl.duration(seconds=1))
    actual = select_volume_universe(pl.concat([first, later]), VolumeUniverseRule(30, 1, .8, 50))
    assert actual["listing_id"].to_list() == ["a", "a"]
