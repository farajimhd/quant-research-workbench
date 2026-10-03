import math

import polars as pl
import pytest

from pipelines.strategy_one.strategy_forty_three_facts import derive_completed_features


def grid(lows, *, missing=()):
    return pl.DataFrame({"boundary_ms": [1000 * (i + 1) for i in range(len(lows))],
        "observed": [i not in missing for i in range(len(lows))],
        "close": [10. + i / 10 for i in range(len(lows))],
        "low": lows, "high": [11.] * len(lows),
        "dollar_volume": [1000. + i for i in range(len(lows))]})


def test_confirmation_is_available_only_after_its_confirmation_decision():
    rows = derive_completed_features(grid([10., 9., 8., 9., 10., 11., 12.]))
    assert rows["swing_low"].to_list()[:5] == [None] * 5
    assert rows["swing_low"][5] == rows["swing_low"][6] == 8.
    assert rows["swing_available_ms"][5] == 5000
    tied = derive_completed_features(grid([8., 8., 8., 8., 8., 8.]))
    assert tied["swing_low"][5] == 8.


def test_missing_seconds_invalidate_complete_windows_without_carrying_prices():
    rows = derive_completed_features(grid([10., 9., 8., 9., 10.] + [10.] * 20,
        missing=(2, 13)))
    assert rows["swing_low"][5] is None
    assert rows["ten_second_mean_movement"][12] is None
    assert rows["ten_second_mean_movement"][23] is None
    assert math.isclose(rows["ten_second_mean_movement"][24], .1)


def test_features_are_prefix_causal_and_attention_excludes_current_second():
    source = grid([10., 9., 8., 9., 10.] + [10.] * 20)
    entire = derive_completed_features(source)
    for length in (5, 11, 15, 24):
        assert derive_completed_features(source.head(length)).equals(entire.head(length))
    assert entire["previous_ten_second_mean_notional"][10] == 1004.5
    assert entire["previous_five_second_close"][10] == 10.5


def test_sparse_unordered_or_non_session_grid_fails_closed():
    source = grid([10.] * 15)
    for bad in (source.slice(1), source.filter(pl.col("boundary_ms") != 5000), source.reverse()):
        with pytest.raises(ValueError, match="consecutive"):
            derive_completed_features(bad)
