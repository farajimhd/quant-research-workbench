"""Prepared rule validation; these tests do not grant runtime admission."""
import random

import numpy as np
import pytest

from src.trading_runtime.strategy_entry_activity_fade import (
    AFTERHOURS_START_MS, EntryActivityCandle, MAX_TRADE_COUNT,
    entry_activity_allowed, entry_activity_mask,
)


def candles(boundary, counts):
    latest = boundary // 5000 * 5000
    return tuple(EntryActivityCandle(latest - offset, count)
                 for offset, count in zip((15000, 10000, 5000, 0), counts))


@pytest.mark.parametrize('opening', [0, AFTERHOURS_START_MS])
@pytest.mark.parametrize('counts,expected', [
    ((174, 121, 34, 60), False),  # Actual development VGAS native 5s counts.
    ((109, 145, 21, 81), False),  # Actual development CAST native 5s counts.
    ((100, 100, 50, 50), False),
    ((100, 101, 50, 51), True),  # Odd prior sum: strict integer boundary.
    ((0, 0, 0, 0), True),
    ((0, 0, 1, 0), True),
    ((1, 0, 0, 0), False),
    ((MAX_TRADE_COUNT, MAX_TRADE_COUNT, MAX_TRADE_COUNT, 0), False),
    ((MAX_TRADE_COUNT, MAX_TRADE_COUNT, MAX_TRADE_COUNT, 1), True),
])
def test_exact_half_comparison_and_session(opening, counts, expected):
    boundary = opening + 25_100
    assert entry_activity_allowed(boundary, candles(boundary, counts)) is expected


def test_missing_zero_and_history_activation_are_distinct():
    assert entry_activity_allowed(19_900, (None,) * 4)
    assert not entry_activity_allowed(20_000, (None,) * 4)
    assert entry_activity_allowed(20_000, candles(20_000, (0, 0, 0, 0)))
    assert not entry_activity_allowed(20_000, candles(20_000, (10, 10, 0, 0)))


@pytest.mark.parametrize('boundary', [0, 19_800_000, 25_000_000, AFTERHOURS_START_MS, 57_600_000])
def test_no_entry_at_session_close_or_during_regular_hours(boundary):
    assert not entry_activity_allowed(boundary, candles(boundary, (0, 0, 10, 10)))


@pytest.mark.parametrize('replacement', [5000, 15000, 30000, -1])
def test_stale_reordered_future_or_missing_clock_rejects(replacement):
    observed = list(candles(25_100, (0, 0, 10, 10)))
    observed[2] = EntryActivityCandle(replacement, 10)
    assert not entry_activity_allowed(25_100, tuple(observed))


def test_scalar_vector_parity_and_uint64_overflow_against_python_integers():
    rng = random.Random(36)
    values = [[rng.randrange(1 << 64) for _ in range(4)] for _ in range(512)]
    boundaries = np.arange(25_000, 25_000 + 51200, 100, dtype=np.int64)
    windows = [candles(int(boundary), counts) for boundary, counts in zip(boundaries, values)]
    clocks = np.asarray([[c.boundary_ms for c in row] for row in windows], dtype=np.int64)
    counts = np.asarray(values, dtype=np.uint64)
    presence = np.ones((512, 4), dtype=np.bool_)
    originals = [a.copy() for a in (boundaries, clocks, counts, presence)]
    actual = entry_activity_mask(boundaries, clocks, counts, presence)
    expected = [not (a + b > 0 and 2 * (c + d) <= a + b) for a, b, c, d in values]
    assert actual.tolist() == expected
    assert [entry_activity_allowed(int(b), row) for b, row in zip(boundaries, windows)] == expected
    for before, after in zip(originals, (boundaries, clocks, counts, presence)):
        np.testing.assert_array_equal(before, after)


def test_presence_and_shape_validation():
    clocks = np.asarray([[10000, 15000, 20000, 25000]], dtype=np.int64)
    counts = np.asarray([[10, 10, 20, 20]], dtype=np.uint64)
    assert not entry_activity_mask(np.asarray([25100], dtype=np.int64), clocks, counts,
                                   np.asarray([[True, False, True, True]]))[0]
    with pytest.raises(ValueError):
        entry_activity_mask(np.asarray([25100], dtype=np.int64), clocks, counts.astype(np.int64),
                            np.ones((1, 4), dtype=np.bool_))
    with pytest.raises(ValueError):
        entry_activity_mask(np.asarray([25101], dtype=np.int64), clocks, counts,
                            np.ones((1, 4), dtype=np.bool_))
    with pytest.raises(ValueError):
        entry_activity_allowed(25100, candles(25100, (True, 0, 10, 10)))
