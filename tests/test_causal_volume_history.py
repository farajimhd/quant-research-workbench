from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from src.market_engine.causal_volume_history import elapsed_volume_history
from src.market_engine.volume_rank_rules import VolumeUniverseRule, select_volume_universe


def clocks(volumes, listing="a", day=date(2026, 8, 3)):
    start = datetime.combine(day, datetime.min.time(), timezone.utc)
    times = [start + timedelta(seconds=i) for i in range(len(volumes))]
    return pl.DataFrame({"listing_id": [listing] * len(times),
                         "session_date": [day] * len(times),
                         "boundary_at": times, "available_at": times, "volume": volumes})


def test_elapsed_window_includes_current_second_and_zero_clocks():
    result = elapsed_volume_history(clocks([10, 0, 0, 5, 0]), 3)
    assert result["rolling_volume"].to_list() == [10, 10, 10, 5, 5]


def test_interleaved_listings_and_sessions_have_independent_windows():
    frame = pl.concat([clocks([10, 0]), clocks([3, 4], "b"),
                       clocks([2, 0], day=date(2026, 8, 4))]).reverse()
    result = elapsed_volume_history(frame, 30)
    assert result["rolling_volume"].to_list() == [10, 10, 2, 2, 3, 7]


def test_producer_output_feeds_shared_scanner_without_current_trade_requirement():
    frame = pl.concat([clocks([10, 0]), clocks([5, 0], "b")]).with_columns(
        pl.col("listing_id").str.to_uppercase().alias("ticker"),
        (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
        pl.lit(10.).alias("adjusted_prior_close"))
    ranked = select_volume_universe(elapsed_volume_history(frame, 30),
                                    VolumeUniverseRule(30, 1, .8, 50))
    assert ranked["listing_id"].to_list() == ["a", "a"]
    assert ranked["volume"].to_list() == [10, 0]


def test_share_counts_fail_closed_before_float_precision_loss():
    with pytest.raises(ValueError):
        elapsed_volume_history(clocks([2 ** 53 + 1]), 30)
    with pytest.raises(ValueError):
        elapsed_volume_history(clocks([2 ** 53, 2]), 30)


def test_canonical_fractional_shares_retain_ranking_and_elapsed_sum():
    frame = pl.concat([clocks([4.8366000056266785, .8734139800071716]),
                       clocks([4.5, .5], "b")]).with_columns(
        pl.col("listing_id").str.to_uppercase().alias("ticker"),
        (pl.col("boundary_at") - pl.duration(days=1)).alias("prior_close_available_at"),
        pl.lit(10.).alias("adjusted_prior_close"))
    history = elapsed_volume_history(frame, 30)
    assert history.filter(pl.col("listing_id") == "a")["rolling_volume"].to_list() == [
        4.8366000056266785, 5.71001398563385]
    ranked = select_volume_universe(history, VolumeUniverseRule(30, 1, .8, 50))
    assert ranked["listing_id"].to_list() == ["a", "a"]


@pytest.mark.parametrize("kind", ["gap", "duplicate", "future", "negative", "nonfinite"])
def test_bad_coverage_and_invalid_shares_fail_closed(kind):
    frame = clocks([1., 2., 3.])
    if kind == "gap":
        frame = frame.filter(pl.col("volume") != 2)
    elif kind == "duplicate":
        frame = pl.concat([frame, frame.head(1)])
    elif kind == "future":
        frame = frame.with_columns(pl.col("available_at") + pl.duration(seconds=1))
    elif kind == "negative":
        frame = frame.with_columns(pl.lit(-1.).alias("volume"))
    else:
        frame = frame.with_columns(pl.lit(float("inf")).alias("volume"))
    with pytest.raises(ValueError):
        elapsed_volume_history(frame, 30)
