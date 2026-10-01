"""Strategy 17 eligibility from exact completed producer MACD observations.

This module compares observations; it does not calculate MACD or authorize
orders. The 10s histogram must be positive and grow strictly beyond the
previous histogram plus ten percent of its absolute magnitude.
"""
from __future__ import annotations

import numpy as np

from .strategy_rising_momentum_entry import rising_momentum_entry_mask
from .strategy_rising_momentum_witness import RisingMomentumWitness, validate_momentum_witness

POLICY_ID = 'strategy-seventeen-positive-ten-second-histogram-growth-10pct-v1'
HISTOGRAM_GROWTH_FRACTION = 0.10


def strong_ten_second_momentum_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'fraction': HISTOGRAM_GROWTH_FRACTION,
        'resolution_ms': 10_000,
        'comparison': 'current_histogram > 0 and current_histogram > prior_histogram + 0.10 * abs(prior_histogram)',
        'scope': 'entry_and_reentry_only',
        'clock': 'completed_adjacent_producer_observations',
    }


def strong_ten_second_momentum_entry_mask(
    boundaries_ms: np.ndarray, current_boundaries_ms: np.ndarray,
    prior_boundaries_ms: np.ndarray, current_line: np.ndarray,
    current_signal: np.ndarray, prior_line: np.ndarray,
    prior_signal: np.ndarray,
) -> np.ndarray:
    """Return shape (N,) eligibility from source arrays shaped (N,2).

    Columns are ordered 1s,10s. The existing reducer validates exact completed
    adjacent clocks, missing encodings and Float64 precision for both columns.
    Only column1 contributes to this rule. Every row is independent; inputs
    remain unchanged. Missing 10s observations reject without carrying older
    values, even if the 1s observation is rising.
    """
    rising_momentum_entry_mask(
        boundaries_ms, current_boundaries_ms, prior_boundaries_ms,
        current_line, current_signal, prior_line, prior_signal)
    current = np.asarray(current_boundaries_ms)[:, 1]
    prior = np.asarray(prior_boundaries_ms)[:, 1]
    line, signal, old_line, old_signal = (
        np.asarray(value)[:, 1] for value in
        (current_line, current_signal, prior_line, prior_signal))
    available = ((current > 0) & (prior > 0) & np.isfinite(line)
                 & np.isfinite(signal) & np.isfinite(old_line) & np.isfinite(old_signal))
    with np.errstate(over='ignore', invalid='ignore'):
        histogram = line - signal
        previous = old_line - old_signal
        threshold = previous + HISTOGRAM_GROWTH_FRACTION * np.abs(previous)
    if np.any(available & (~np.isfinite(histogram) | ~np.isfinite(previous)
                           | ~np.isfinite(threshold))):
        raise ValueError('Strong 10s momentum comparison overflows')
    return available & (histogram > 0) & (histogram > threshold)


def strong_ten_second_momentum_entry(witness: RisingMomentumWitness) -> bool:
    """Validate typed scalar authority and reuse the native (1,2) reducer."""
    validate_momentum_witness(witness)
    clocks = tuple(np.asarray([
        [getattr(observation, name) for observation in witness.observations]],
        dtype=np.int64) for name in ('current_boundary_ms', 'prior_boundary_ms'))
    values = tuple(np.asarray([
        [np.nan if getattr(observation, name) is None else getattr(observation, name)
         for observation in witness.observations]], dtype=np.float64)
        for name in ('current_line', 'current_signal', 'prior_line', 'prior_signal'))
    return bool(strong_ten_second_momentum_entry_mask(
        np.asarray([witness.boundary_ms], dtype=np.int64), *clocks, *values)[0])
