"""Check the model input contract against historical XNYS calendar boundaries."""

import exchange_calendars as xc
import numpy as np
import pandas as pd
import pytest

from research.bar_gpt.v5.transforms_v1 import BarInputTransformV1, FEATURE_NAMES, TIMEZONE


@pytest.fixture(scope="module")
def transform():
    """Use actual holiday, DST, and early-close dates across the tested years."""
    schedule = xc.get_calendar("XNYS", start="2020-01-01", end="2024-12-31").schedule
    return BarInputTransformV1(schedule)


def clocks(start, frequency="1min"):
    """Return 64 consecutive local bar ends as UTC microseconds [1,64]."""
    return pd.date_range(start, periods=64, freq=frequency, tz=TIMEZONE).as_unit("us").asi8[None, :]


def apply(transform, ends, frame=60_000_000):
    """Supply neutral market observations to isolate time transformations."""
    shape = (*ends.shape, 21)
    return transform(np.ones(shape, dtype=np.float32), np.ones(shape, dtype=bool), ends, frame)


def feature(result, name):
    """Select a named feature without duplicating the module's column ordering."""
    return result.values[..., FEATURE_NAMES.index(name)]


def test_market_preservation_and_missing_evidence(transform):
    """Missing source fields and an unavailable predecessor have distinct masks."""
    values = np.arange(64*21, dtype=np.float32).reshape(1, 64, 21)
    valid = np.ones_like(values, dtype=np.uint8)
    values[0, 0, 0], valid[0, 0, 0] = np.nan, 0
    original = values.copy()
    result = transform(values, valid, clocks("2024-03-11 09:30"), 60_000_000)
    assert result.values.shape == result.valid.shape == (1, 64, 33)
    assert result.values.dtype == np.float32 and result.valid.dtype == bool
    np.testing.assert_array_equal(result.values[..., :21], np.where(valid, values, 0))
    np.testing.assert_array_equal(values, original)
    np.testing.assert_array_equal(result.valid[..., :21], valid)
    assert not result.valid[0, 0, FEATURE_NAMES.index("log_gap_ratio")]
    assert feature(result, "log_gap_ratio")[0, 0] == 0
    np.testing.assert_allclose(feature(result, "log_gap_ratio")[0, 1:], np.log(2))
    np.testing.assert_allclose(feature(result, "bar_index")[0, [0, -1]], [0, 1])


@pytest.mark.parametrize("start,expected", [
    ("2024-03-11 09:30", [[1, 0, 0], [0, 1, 0]]),
    ("2024-11-29 13:00", [[0, 1, 0], [0, 0, 1]]),
])
def test_session_boundaries_and_early_close(transform, start, expected):
    """Boundary contact alone must not mark an adjacent session as overlapping."""
    result = apply(transform, clocks(start))
    np.testing.assert_array_equal(result.values[0, :2, 21:24], expected)


def test_dst_preserves_local_clock(transform):
    """Identical NY clock times encode equally on both sides of the DST change."""
    ends = np.concatenate((clocks("2024-03-08 10:00"), clocks("2024-03-11 10:00")))
    result = apply(transform, ends)
    for name in ("intraday_sin", "intraday_cos", "premarket", "regular_hours", "after_hours"):
        np.testing.assert_array_equal(feature(result, name)[0], feature(result, name)[1])


def test_daily_week53_and_weekend_gap(transform):
    """Daily intervals use the extended session and preserve calendar elapsed time."""
    dates = xc.get_calendar("XNYS", start="2020-10-01", end="2021-02-01").sessions[:64]
    ends = (dates.tz_localize(None) + pd.Timedelta(hours=20)).tz_localize(TIMEZONE).as_unit("us").asi8[None, :]
    result = apply(transform, ends, 86_400_000_000)
    np.testing.assert_array_equal(result.values[..., 21:24], 1)
    index = dates.get_loc(pd.Timestamp("2020-12-31"))
    np.testing.assert_allclose(feature(result, "iso_week_sin")[0, index], np.sin(2*np.pi*52/53), atol=1e-7)
    np.testing.assert_allclose(feature(result, "log_gap_ratio")[0, 1:], np.log1p(np.diff(ends[0])/86_400_000_000))


def test_hourly_overlap_and_mixed_batch(transform):
    """An hour crossing the opening bell belongs to both sessions; batching is invariant."""
    days = ("2024-03-11", "2024-03-12", "2024-03-13", "2024-03-14")
    hourly = np.concatenate([
        pd.date_range(f"{day} 05:00", periods=16, freq="1h", tz=TIMEZONE).as_unit("us").asi8
        for day in days
    ])[None, :]
    minute = clocks("2024-03-11 10:00")
    result = apply(transform, np.concatenate((hourly, minute)), np.array([3_600_000_000, 60_000_000]))
    np.testing.assert_array_equal(result.values[0, 5, 21:24], [1, 1, 0])
    np.testing.assert_array_equal(result.values[1], apply(transform, minute).values[0])


@pytest.mark.parametrize("start,frequency,frame", [
    ("2024-03-11 10:00", "100ms", 100_000),
    ("2024-03-11 10:00", "1s", 1_000_000),
    ("2024-03-11 10:00", "10s", 10_000_000),
])
def test_subminute_frames(transform, start, frequency, frame):
    """Every supported fine timeframe retains its scale and normal adjacent gap."""
    result = apply(transform, clocks(start, frequency), frame)
    np.testing.assert_allclose(feature(result, "log_timeframe_seconds"), np.log1p(frame/1e6))
    np.testing.assert_allclose(feature(result, "log_gap_ratio")[0, 1:], np.log(2))


@pytest.mark.parametrize("start", ["2024-07-04 10:00", "2024-03-11 03:00", "2024-03-11 10:00:01"])
def test_invalid_session_or_grid_rejected(transform, start):
    """Unknown trading dates, out-of-session bars, and off-grid ends fail closed."""
    with pytest.raises(ValueError):
        apply(transform, clocks(start))


def test_bad_evidence_and_order_rejected(transform):
    """Reject observed NaNs, malformed evidence, and duplicate clocks explicitly."""
    ends = clocks("2024-03-11 10:00")
    values = np.ones((1, 64, 21), dtype=np.float32)
    valid = np.ones_like(values, dtype=np.uint8)
    values[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        transform(values, valid, ends, 60_000_000)
    values[0, 0, 0], valid[0, 0, 0] = 1, 2
    with pytest.raises(ValueError, match="evidence"):
        transform(values, valid, ends, 60_000_000)
    ends[0, 1] = ends[0, 0]
    with pytest.raises(ValueError, match="increase"):
        apply(transform, ends)
