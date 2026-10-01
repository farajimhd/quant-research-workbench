"""Staged first-setup completed-price comparison; no runtime admission.

Certified native compilation must select the earliest structural setup before
this reducer is called. Source identity/chronology alone cannot prove initiality.
Only premarket first setups add the comparison; after-hours retains its parent.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

import numpy as np

POLICY_ID = 'strategy-twenty-premarket-first-completed-one-second-price-break-v1'
PREMARKET_END_MS = 19_800_000
RESOLUTION_MS = 1_000


def initial_price_break_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'session_scope': 'premarket_only',
        'comparison': 'first_completed_1s_close_int > immediately_prior_1s_high_int',
        'clock': 'adjacent_completed_producer_bars_at_first_structural_setup',
        'missing_policy': 'reject_without_carrying_older_bars',
        'first_momentum': 'unchanged_parent19_premarket_50pct_afterhours_10pct',
        'current_momentum': 'unchanged_parent19_strict_10pct',
        'afterhours_policy': 'unchanged_parent19',
        'initiality_authority': 'certified_native_candidate_compiler',
    }


def first_setup_price_break_mask(
    first_boundaries_ms: np.ndarray,
    current_boundaries_ms: np.ndarray,
    prior_boundaries_ms: np.ndarray,
    current_close_int: np.ndarray,
    prior_high_int: np.ndarray,
    current_price_valid: np.ndarray,
    prior_extremes_valid: np.ndarray,
) -> np.ndarray:
    """Return immutable eligibility (N,) from aligned producer arrays (N,).

    Clocks are int64, prices uint64, flags bool. Integer comparison preserves
    producer precision beyond Float64's exact range. Zero clocks mean missing
    bars and require zero values/false flags. Present bars must end exactly at
    the completed 1s boundary or its immediate predecessor, never an older bar.
    No price source is required outside the premarket scope; typed encodings and
    chronology still validate there. Inputs remain unchanged, including empty N.
    """
    arrays = (first_boundaries_ms, current_boundaries_ms, prior_boundaries_ms,
              current_close_int, prior_high_int, current_price_valid,
              prior_extremes_valid)
    dtypes = (np.int64, np.int64, np.int64, np.uint64, np.uint64,
              np.bool_, np.bool_)
    if (any(type(value) is not np.ndarray or value.ndim != 1
            or value.dtype != dtype for value, dtype in zip(arrays, dtypes))
            or any(value.shape != first_boundaries_ms.shape for value in arrays)):
        raise ValueError('First price break requires aligned exact producer arrays')
    first, current, prior, close, high, valid_close, valid_high = arrays
    if np.any(first <= 0) or np.any(first > 57_600_000) or np.any(first % 100):
        raise ValueError('First price break has invalid completed first clocks')
    expected_current = first // RESOLUTION_MS * RESOLUTION_MS
    expected_prior = np.maximum(expected_current - RESOLUTION_MS, 0)
    if (np.any(current < 0) or np.any(prior < 0)
            or np.any((current != 0) & (current != expected_current))
            or np.any((prior != 0) & (prior != expected_prior))
            or np.any((current == 0) & ((close != 0) | valid_close))
            or np.any((prior == 0) & ((high != 0) | valid_high))
            or np.any(valid_close & (close == 0))
            or np.any(valid_high & (high == 0))):
        raise ValueError('First price break has invalid completed source clocks or values')
    in_scope = first < PREMARKET_END_MS
    eligible = (~in_scope | ((current > 0) & (prior > 0)
                            & valid_close & valid_high & (close > high)))
    return np.frombuffer(np.ascontiguousarray(eligible).tobytes(), dtype=np.bool_)


@dataclass(frozen=True, slots=True)
class FirstSetupPriceBreakWitness:
    ticker: str
    first_setup_boundary_ms: int
    source_build_id: str
    bars_attempt_id: str
    market_plan_token: str
    current_boundary_ms: int
    prior_boundary_ms: int
    current_close_int: int
    prior_high_int: int
    current_price_valid: bool
    prior_extremes_valid: bool


def first_setup_price_break(witness: FirstSetupPriceBreakWitness) -> bool:
    """Validate source-bound scalar types and reuse the native one-row reducer.

    The future compiler must additionally bind this first clock and source to
    its original momentum selection; this scalar does not authorize a trade.
    """
    if (type(witness) is not FirstSetupPriceBreakWitness
            or type(witness.ticker) is not str
            or re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', witness.ticker) is None
            or type(witness.source_build_id) is not str
            or re.fullmatch(r'[0-9a-f]{64}(?:-[0-9a-f]{12})?', witness.source_build_id) is None
            or type(witness.market_plan_token) is not str
            or re.fullmatch(r'[0-9a-f]{64}', witness.market_plan_token) is None
            or type(witness.bars_attempt_id) is not str):
        raise ValueError('First price break requires exact source identity')
    try:
        if (str(UUID(witness.bars_attempt_id)) != witness.bars_attempt_id
                or UUID(witness.bars_attempt_id).int == 0):
            raise ValueError('Noncanonical attempt')
    except (ValueError, AttributeError) as exc:
        raise ValueError('First price break requires exact source attempt') from exc
    clocks = (witness.first_setup_boundary_ms, witness.current_boundary_ms,
              witness.prior_boundary_ms)
    prices = (witness.current_close_int, witness.prior_high_int)
    flags = (witness.current_price_valid, witness.prior_extremes_valid)
    if (any(type(value) is not int or not 0 <= value <= 57_600_000 for value in clocks)
            or any(type(value) is not int or not 0 <= value <= np.iinfo(np.uint64).max
                   for value in prices)
            or any(type(value) is not bool for value in flags)):
        raise ValueError('First price break requires exact scalar producer values')
    arrays = tuple(np.asarray([value], dtype=dtype) for value, dtype in zip(
        (*clocks, *prices, *flags),
        (np.int64, np.int64, np.int64, np.uint64, np.uint64, np.bool_, np.bool_)))
    return bool(first_setup_price_break_mask(*arrays)[0])
