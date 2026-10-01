"""Strategy18 freezes strong momentum at the first structurally eligible setup.

The native compiler selects initiality from the complete certified candidate
population. Scalar witnesses bind source identity and chronology, but cannot
prove that a supplied first setup is the earliest eligible setup by themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .strategy_rising_momentum_witness import RisingMomentumWitness, validate_momentum_witness
from .strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry

POLICY_ID = 'strategy-eighteen-first-strong-momentum-setup-v1'


def initial_strong_momentum_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'first_setup': 'first_structurally_eligible_setup',
        'group': 'ticker_and_activation_episode_start_ms',
        'selection': 'earliest_completed_candidate_boundary_ms',
        'freeze': 'first_setup_strong_momentum_result_for_entire_activation_episode',
        'eligibility': 'base_eligible and current_strong and first_setup_strong',
        'scope': 'entry_and_reentry_only',
        'initiality_authority': 'certified_native_candidate_compiler',
    }


def _immutable(array: np.ndarray) -> np.ndarray:
    return np.frombuffer(np.ascontiguousarray(array).tobytes(), dtype=array.dtype)


def initial_strong_momentum_entry_mask(
    ticker_codes: np.ndarray, episode_start_ms: np.ndarray,
    boundary_ms: np.ndarray, base_eligible: np.ndarray,
    current_strong: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return immutable first indices (N,) and eligibility (N,) in input order.

    Codes/episode starts/boundaries are exact int64 (N,) arrays; flags are bool
    (N,). Each first index refers to the original input row with the earliest
    base-eligible clock in that ticker/activation episode. -1 means the episode
    has no base-eligible row. Selection is independent of input ordering and
    does not reset after a close or re-entry. A weak first setup permanently
    rejects later strong setups in that activation episode.

    Sorting/group reductions run natively; there is no Python candidate loop.
    Noneligible rows may reference a later first index, but can never admit an
    entry. Scalar initiality must be supplied by this certified compilation.
    """
    arrays = (ticker_codes, episode_start_ms, boundary_ms, base_eligible, current_strong)
    if (any(not isinstance(value, np.ndarray) or value.ndim != 1 for value in arrays)
            or any(value.dtype != np.int64 for value in arrays[:3])
            or any(value.dtype != np.bool_ for value in arrays[3:])
            or any(value.shape != ticker_codes.shape for value in arrays)
            or np.any(ticker_codes < 0)
            or np.any(episode_start_ms <= 0) or np.any(episode_start_ms > 57_600_000)
            or np.any(episode_start_ms % 100)
            or np.any(boundary_ms <= 0) or np.any(boundary_ms > 57_600_000)
            or np.any(boundary_ms % 100) or np.any(episode_start_ms > boundary_ms)):
        raise ValueError('Initial momentum requires aligned typed completed candidate arrays')
    count = len(ticker_codes)
    if not count:
        return _immutable(np.empty(0, dtype=np.int64)), _immutable(np.empty(0, dtype=np.bool_))
    order = np.lexsort((boundary_ms, episode_start_ms, ticker_codes))
    tickers, starts, boundaries = (value[order] for value in arrays[:3])
    new_group = np.r_[True, (tickers[1:] != tickers[:-1]) | (starts[1:] != starts[:-1])]
    if np.any(~new_group[1:] & (boundaries[1:] == boundaries[:-1])):
        raise ValueError('Initial momentum candidate ticker/episode/boundary is duplicated')
    group_starts = np.flatnonzero(new_group)
    positions = np.where(base_eligible[order], np.arange(count, dtype=np.int64), count)
    first_positions = np.minimum.reduceat(positions, group_starts)
    group_first = np.where(first_positions < count, order[np.minimum(first_positions, count - 1)], -1)
    sorted_group = np.cumsum(new_group, dtype=np.int64) - 1
    first_indices = np.empty(count, dtype=np.int64)
    first_indices[order] = group_first[sorted_group]
    initial_strong = (first_indices >= 0) & current_strong[np.maximum(first_indices, 0)]
    eligible = base_eligible & current_strong & initial_strong
    return _immutable(first_indices), _immutable(eligible)


@dataclass(frozen=True, slots=True)
class InitialStrongMomentumWitness:
    episode_start_ms: int
    first_setup: RisingMomentumWitness


def validate_initial_strong_momentum_witness(
    current: RisingMomentumWitness, initial: InitialStrongMomentumWitness,
) -> None:
    """Validate exact source identity/chronology; this does not prove initiality.

    Eligibility is evaluated separately by the shared Strategy17 predicate on
    both observations. The compiler owns earliest base-eligible selection.
    """
    if (type(initial) is not InitialStrongMomentumWitness
            or type(initial.episode_start_ms) is not int
            or not 0 < initial.episode_start_ms <= 57_600_000
            or initial.episode_start_ms % 100):
        raise ValueError('Initial momentum needs a typed completed activation episode')
    validate_momentum_witness(current)
    validate_momentum_witness(initial.first_setup)
    first = initial.first_setup
    if (initial.episode_start_ms > first.boundary_ms
            or first.boundary_ms > current.boundary_ms
            or any(getattr(first, field) != getattr(current, field) for field in
                   ('ticker', 'source_build_id', 'source_attempt_id', 'market_plan_token'))):
        raise ValueError('Initial momentum source identity or causal clocks differ')


def initial_strong_momentum_entry(
    current: RisingMomentumWitness, initial: InitialStrongMomentumWitness,
) -> bool:
    """Require both unchanged Strategy17 predicates after typed source checks."""
    validate_initial_strong_momentum_witness(current, initial)
    # Evaluate both, including current source integrity when the first is weak.
    first_strong = strong_ten_second_momentum_entry(initial.first_setup)
    current_strong = strong_ten_second_momentum_entry(current)
    return first_strong and current_strong
