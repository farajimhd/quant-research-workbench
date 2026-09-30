"""Pure completed-source momentum direction rule proposed for Strategy 13.

This compares producer MACD observations; it does not calculate MACD. No
published strategy dispatches this rule until its source and journal evidence
are certified. A positive result is eligibility, never order authorization.
"""
from __future__ import annotations

import numpy as np


MOMENTUM_RESOLUTIONS_MS = (1_000, 10_000)


def rising_momentum_entry_mask(
    boundaries_ms: np.ndarray, current_boundaries_ms: np.ndarray,
    prior_boundaries_ms: np.ndarray, current_line: np.ndarray,
    current_signal: np.ndarray, prior_line: np.ndarray,
    prior_signal: np.ndarray,
) -> np.ndarray:
    """Return (N,) eligibility from clocks/values shaped (N, 2): 1s then 10s.

    Each branch uses the exact most recent completed bucket and its adjacent
    predecessor. Zero clocks with NaN values represent unavailable observations;
    that branch rejects. An older bucket is never carried forward. All arrays
    are read-only inputs. Native row-independent comparisons preserve prefixes.
    """
    boundaries = np.asarray(boundaries_ms)
    current = np.asarray(current_boundaries_ms)
    prior = np.asarray(prior_boundaries_ms)
    values = tuple(np.asarray(value) for value in (
        current_line, current_signal, prior_line, prior_signal))
    shape = (len(boundaries), 2) if boundaries.ndim == 1 else None
    if (shape is None or boundaries.dtype.kind not in "iu"
            or np.any(boundaries <= 0) or np.any(boundaries > 57_600_000)
            or np.any(boundaries % 100)
            or any(clock.shape != shape or clock.dtype.kind not in "iu"
                   for clock in (current, prior))
            or any(value.shape != shape or value.dtype != np.float64
                   or np.any(np.isinf(value)) for value in values)):
        raise ValueError("Rising momentum needs aligned completed source arrays")
    resolutions = np.asarray(MOMENTUM_RESOLUTIONS_MS, dtype=np.int64)
    # Shapes (N, 1) and (2,) broadcast to exact completed clocks (N, 2).
    expected = boundaries.astype(np.int64, copy=False)[:, None] // resolutions * resolutions
    expected_prior = expected - resolutions
    if (np.any((current != 0) & (current != expected))
            or np.any((prior != 0) & ((prior != expected_prior) | (expected_prior <= 0)))
            or np.any((current == 0) & (prior != 0))):
        raise ValueError("Rising momentum source is forming, stale or nonadjacent")
    line, signal, old_line, old_signal = values
    # Missing/null source observations are encoded consistently, not coerced
    # to a zero histogram that could create a spurious rising branch.
    for clock, left, right in ((current, line, signal), (prior, old_line, old_signal)):
        if np.any((clock == 0) & (~np.isnan(left) | ~np.isnan(right))):
            raise ValueError("Missing momentum source has invented values")
    available = ((current > 0) & (prior > 0) & np.isfinite(line)
                 & np.isfinite(signal) & np.isfinite(old_line) & np.isfinite(old_signal))
    with np.errstate(over="ignore", invalid="ignore"):
        histogram = line - signal
        prior_histogram = old_line - old_signal
    if np.any(available & (~np.isfinite(histogram) | ~np.isfinite(prior_histogram))):
        raise ValueError("Rising momentum histogram comparison overflows")
    rising = available & (histogram > prior_histogram)
    return np.any(rising, axis=1)
