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


def test_frozen_top10_matches_original_search_across_ties_and_elapsed_windows():
    import numpy as np
    from research.vectorized_backtest.v6.torch_backtest.materialize import rolling_volume, rank_indices
    # Synthetic qualification only: integer SIP share volumes, complete elapsed
    # clocks, listing identities sorted independently of ticker spelling.
    clocks, listings = 93, 18
    volumes = np.random.default_rng(223).integers(0, 20, size=(clocks, listings)).astype(np.float64)
    volumes[:3] = 0
    volumes[:, 6:15] = volumes[:, 5:6]  # tie extends across the tenth-place cutoff
    volumes[:, 16:] = 100000  # ineligible prices must not occupy ranking slots
    prices = np.full(listings, 10.)
    prices[0], prices[1], prices[16], prices[17] = .8, 50., .79, 50.01
    eligible = np.flatnonzero((prices >= .8) & (prices <= 50.))
    reference, scores = rank_indices(volumes[:, eligible], None, 30, 10)
    rolling = rolling_volume(volumes, 30)
    start = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    records = [dict(listing_id=f'listing-{column:02}', ticker=f'T{listings-column:02}',
        boundary_at=start + timedelta(seconds=clock + 1),
        available_at=start + timedelta(seconds=clock + 1),
        prior_close_available_at=start - timedelta(days=3),
        adjusted_prior_close=prices[column], rolling_volume=rolling[clock, column],
        volume_window_seconds=30)
        for clock in range(clocks) for column in range(listings)]
    actual = select_volume_universe(pl.DataFrame(records).reverse(), VolumeUniverseRule(30, 10, .8, 50))
    for clock in range(clocks):
        observed = actual.filter(pl.col('boundary_at') == start + timedelta(seconds=clock + 1))
        keep = reference[clock] >= 0
        expected = [f'listing-{eligible[column]:02}' for column in reference[clock, keep]]
        assert observed['listing_id'].to_list() == expected
        assert observed['rolling_volume'].to_list() == scores[clock, keep].tolist()
    assert actual.filter(pl.col('boundary_at') <= start + timedelta(seconds=3)).is_empty()
    for cutoff in (4, 30, 31, 62):
        end = start + timedelta(seconds=cutoff)
        short = select_volume_universe(pl.DataFrame(records).filter(pl.col('boundary_at') <= end),
            VolumeUniverseRule(30, 10, .8, 50))
        assert short.equals(actual.filter(pl.col('boundary_at') <= end))
