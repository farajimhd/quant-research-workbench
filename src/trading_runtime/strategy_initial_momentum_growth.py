"""Staged Strategy19 first-setup growth primitive, without registration.

Compare exact completed producer observations; never calculate MACD. Only the
premarket first structurally eligible setup uses 50 percent growth. Current entries keep
the shared strict10 percent rule; certified native compilation owns selection.
"""
from __future__ import annotations

import numpy as np

from .strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
from .strategy_rising_momentum_witness import RisingMomentumWitness, validate_momentum_witness

POLICY_ID = 'strategy-nineteen-premarket-first-setup-ten-second-histogram-growth-50pct-v1'
FIRST_SETUP_GROWTH_FRACTION = 0.50
PREMARKET_END_MS = 19_800_000


def first_setup_momentum_growth_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'fraction': FIRST_SETUP_GROWTH_FRACTION,
        'resolution_ms': 10_000,
        'comparison': 'current_histogram > 0 and current_histogram > prior_histogram + 0.50 * abs(prior_histogram)',
        'scope': 'first_structurally_eligible_setup_only',
        'session_scope': 'premarket_only',
        'afterhours_first_setup_policy': 'unchanged_strict_10pct',
        'boundary_semantics': 'first_structurally_eligible_setup_boundary_ms',
        'clock': 'completed_adjacent_producer_observations',
        'current_entry_policy': 'unchanged_strict_10pct',
        'initiality_authority': 'certified_native_candidate_compiler',
    }


def first_setup_momentum_growth_entry_mask(
    boundaries_ms: np.ndarray, current_boundaries_ms: np.ndarray,
    prior_boundaries_ms: np.ndarray, current_line: np.ndarray,
    current_signal: np.ndarray, prior_line: np.ndarray,
    prior_signal: np.ndarray,
) -> np.ndarray:
    """Return shape (N,) eligibility from source arrays shaped (N,2).

    Columns are ordered 1s,10s. The existing reducer validates exact completed
    adjacent clocks, missing encodings and Float64 precision for both columns.
    Only the 10s column contributes to this rule. Every row is independent; inputs
    remain unchanged. Missing 10s observations reject without carrying older
    values, even if the 1s observation is rising.
    """
    current_rule = strong_ten_second_momentum_entry_mask(
        boundaries_ms, current_boundaries_ms, prior_boundaries_ms,
        current_line, current_signal, prior_line, prior_signal)
    current = np.asarray(current_boundaries_ms)[:, 1]
    prior = np.asarray(prior_boundaries_ms)[:, 1]
    line, signal, old_line, old_signal = (
        np.asarray(value)[:, 1] for value in
        (current_line, current_signal, prior_line, prior_signal))
    available = ((current > 0) & (prior > 0) & np.isfinite(line)
                 & np.isfinite(signal) & np.isfinite(old_line) & np.isfinite(old_signal))
    in_scope = (boundaries_ms > 0) & (boundaries_ms < PREMARKET_END_MS)
    with np.errstate(over='ignore', invalid='ignore'):
        histogram = line - signal
        previous = old_line - old_signal
        threshold = np.zeros_like(previous)
        threshold[in_scope] = previous[in_scope] + FIRST_SETUP_GROWTH_FRACTION * np.abs(previous[in_scope])
    if np.any(in_scope & available & (~np.isfinite(histogram) | ~np.isfinite(previous)
                           | ~np.isfinite(threshold))):
        raise ValueError('First setup 50pct momentum comparison overflows')
    return current_rule & (~in_scope | (available & (histogram > 0) & (histogram > threshold)))


def first_setup_momentum_growth_entry(witness: RisingMomentumWitness) -> bool:
    """Validate typed scalar authority and reuse the native (1,2) reducer."""
    validate_momentum_witness(witness)
    clocks = tuple(np.asarray([
        [getattr(observation, name) for observation in witness.observations]],
        dtype=np.int64) for name in ('current_boundary_ms', 'prior_boundary_ms'))
    values = tuple(np.asarray([
        [np.nan if getattr(observation, name) is None else getattr(observation, name)
         for observation in witness.observations]], dtype=np.float64)
        for name in ('current_line', 'current_signal', 'prior_line', 'prior_signal'))
    return bool(first_setup_momentum_growth_entry_mask(
        np.asarray([witness.boundary_ms], dtype=np.int64), *clocks, *values)[0])
