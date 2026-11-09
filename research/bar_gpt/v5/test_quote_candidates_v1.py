"""Conservative source-to-window selection boundaries, independent of payload repair."""

import numpy as np
import pytest

from .quote_candidates_v1 import file_candidate, overlaps
from .shards.sql import clock


def test_context_envelope_catches_old_context_not_just_origin():
    """A quote at bar t-63 must nominate the saved sample ending at t."""
    intervals = [dict(begin_us=100, end_us=200)]
    assert file_candidate(50, 10000, intervals)
    assert not file_candidate(1000, 10000, intervals)
    assert file_candidate(None, 10000, intervals)
    assert not file_candidate(50, 10000, [])


def test_interval_lookup_matches_brute_force_with_overlap_and_carry():
    """Vectorized prefix maxima handle nested/overlapping carry intervals."""
    rng = np.random.default_rng(5)
    starts = np.sort(rng.integers(100, 100000, 500))
    ends = starts + rng.integers(1, 10000, 500)
    rows = [dict(begin_us=int(a), end_us=int(b)) for a, b in zip(starts, ends)]
    closes = np.arange(1000, 110000, 997, dtype=np.int64)
    width = 1000
    expected = np.array(
        [any(a < t and b >= t - width for a, b in zip(starts, ends)) for t in closes]
    )
    np.testing.assert_array_equal(overlaps(closes, width, rows), expected)


def test_daily_context_starts_at_ny_regular_open():
    """An extended-hours issue after daily close cannot contaminate that day."""
    day = "2020-05-08"
    end = clock(day, 16)
    assert overlaps(
        [end], 86400000000, [dict(begin_us=clock(day, 9), end_us=clock(day, 10))]
    )[0]
    assert not overlaps(
        [end], 86400000000, [dict(begin_us=end + 1, end_us=clock(day, 17))]
    )[0]


def test_unsorted_candidates_fail_closed():
    with pytest.raises(ValueError, match="ordered"):
        overlaps(
            [1000],
            100,
            [dict(begin_us=900, end_us=1000), dict(begin_us=100, end_us=200)],
        )
