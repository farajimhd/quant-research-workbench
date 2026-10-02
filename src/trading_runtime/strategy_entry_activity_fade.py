"""Prepared entry activity rule; no registration or execution admission.

The caller must bind observations to certified producer attempts. This module
only compares supplied completed counts and never reads or creates market data.
"""
from dataclasses import dataclass

import numpy as np

POLICY_ID = 'strategy-thirty-six-completed-entry-activity-fade-v1'
PREMARKET_END_MS = 19_800_000
AFTERHOURS_START_MS = 43_200_000
SESSION_END_MS = 57_600_000
MAX_TRADE_COUNT = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class EntryActivityCandle:
    """One native completed 5s count; an absent candle is a separate value."""
    boundary_ms: int
    trade_count: int


def entry_activity_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'resolution_ms': 5000,
        'comparison': 'reject when prior10 > 0 and 2 * recent10 <= prior10',
        'observations': 'four exact contiguous completed 5s candles in current session',
        'history_activation_ms': 20_000,
        'before_history_activation': 'inherit parent entry eligibility',
        'missing_after_history_activation': 'reject admission; no zero imputation',
        'zero_prior': 'known zero does not establish a percentage decline',
        'scope': 'new entries only; no changes to exits or position sizing',
        'session': 'premarket or afterhours only',
    }


def entry_activity_mask(boundaries_ms, candle_boundaries_ms, trade_counts, observed):
    """Return eligibility (N,) from clocks/counts/presence arrays (N,4).

    Columns are chronological: the first two form prior10 and the last two
    recent10. Presence is independent of price validity: zero-count and
    quote-only producer observations remain actual observations. Exact expected
    clocks prevent stale, forming, reordered or cross-session data from passing.
    Only the first 20 seconds are explicitly outside this additional filter.
    The calculation is vectorized and preserves the full UInt64 count range.
    """
    boundaries = np.asarray(boundaries_ms)
    clocks, counts, ready = map(np.asarray, (candle_boundaries_ms, trade_counts, observed))
    if (boundaries.dtype != np.int64 or boundaries.ndim != 1
            or clocks.dtype != np.int64 or counts.dtype != np.uint64
            or ready.dtype != np.bool_
            or any(a.shape != (len(boundaries), 4) for a in (clocks, counts, ready))):
        raise ValueError('Entry activity requires Int64 clocks, UInt64 counts and Boolean presence')
    if (np.any(boundaries < 0) or np.any(boundaries > SESSION_END_MS)
            or np.any(boundaries % 100 != 0)
            or np.any(np.diff(boundaries) <= 0)):
        raise ValueError('Entry activity decision clocks must be ordered completed 100ms boundaries')
    pm = (boundaries > 0) & (boundaries < PREMARKET_END_MS)
    ah = (boundaries > AFTERHOURS_START_MS) & (boundaries < SESSION_END_MS)
    opening = np.where(ah, AFTERHOURS_START_MS, 0)
    active = boundaries >= opening + 20_000
    latest = boundaries // 5000 * 5000
    # Broadcast each decision's latest completed clock against four offsets.
    expected = latest[:, None] - np.asarray([15_000, 10_000, 5000, 0], dtype=np.int64)
    available = (ready.all(axis=1) & (clocks == expected).all(axis=1)
                 & (clocks[:, 0] - 5000 >= opening))
    # floor((a+b)/2) avoids overflowing the sum of two native UInt64 counts.
    half_prior = (counts[:, 0] // np.uint64(2) + counts[:, 1] // np.uint64(2)
                  + (counts[:, 0] % np.uint64(2) + counts[:, 1] % np.uint64(2)) // np.uint64(2))
    positive_prior = (counts[:, 0] > 0) | (counts[:, 1] > 0)
    # Test c+d <= half_prior without overflowing c+d. The subtraction may wrap
    # where c>half_prior, but the first conjunct rejects exactly those rows.
    faded = (positive_prior & (counts[:, 2] <= half_prior)
             & (counts[:, 3] <= half_prior - counts[:, 2]))
    return (pm | ah) & (~active | (available & ~faded))


def entry_activity_allowed(boundary_ms: int, candles: tuple) -> bool:
    """Typed scalar adapter using the same (1,4) comparison as preparation."""
    if (type(boundary_ms) is not int or not 0 <= boundary_ms <= SESSION_END_MS
            or type(candles) is not tuple or len(candles) != 4):
        raise ValueError('Entry activity requires one decision clock and four candle slots')
    clocks, counts, observed = [], [], []
    for candle in candles:
        if candle is None:
            clocks.append(-1); counts.append(0); observed.append(False)
            continue
        if (type(candle) is not EntryActivityCandle
                or type(candle.boundary_ms) is not int
                or not -(1 << 63) <= candle.boundary_ms < (1 << 63)
                or type(candle.trade_count) is not int
                or not 0 <= candle.trade_count <= MAX_TRADE_COUNT):
            raise ValueError('Entry activity candle does not contain native integer evidence')
        clocks.append(candle.boundary_ms); counts.append(candle.trade_count); observed.append(True)
    return bool(entry_activity_mask(
        np.asarray([boundary_ms], dtype=np.int64),
        np.asarray([clocks], dtype=np.int64), np.asarray([counts], dtype=np.uint64),
        np.asarray([observed], dtype=np.bool_))[0])
