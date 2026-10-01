"""Staged Strategy19 refinement of the existing certified first-setup plan.

This compiler performs no source reads and grants no runtime admission. The
Strategy18 plan remains the sole authority for earliest structural selection;
this refinement strengthens only its premarket anchor's producer comparison.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from hashlib import sha256

import numpy as np

from .backtest_strategy_initial_momentum import (
    CertifiedInitialMomentumPlan, compile_initial_momentum_plan,
)
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_initial_momentum_growth import (
    POLICY_ID, first_setup_momentum_growth_entry_mask,
    first_setup_momentum_growth_entry,
)
from src.trading_runtime.strategy_initial_strong_momentum import InitialMomentumSelectionWitness


def _selection(initial):
    if type(initial) is not CertifiedInitialMomentumPlan:
        raise ValueError("First growth refinement needs the exact certified initial plan")
    momentum = initial.momentum
    # Producer columns have shape (N,2), ordered 1s/10s. Candidate clocks and
    # first indices have shape (N,); only each row's immutable first index
    # supplies the stronger comparison. Current eligibility remains unchanged.
    boundaries = np.fromiter((key[1] for key in momentum.keys), dtype=np.int64)
    first_growth = first_setup_momentum_growth_entry_mask(
        boundaries, momentum.current_boundaries_ms, momentum.prior_boundaries_ms,
        momentum.current_line, momentum.current_signal,
        momentum.prior_line, momentum.prior_signal)
    first = initial.first_indices
    eligible = initial.eligible_mask & (first >= 0)
    if len(first):
        eligible &= first_growth[np.maximum(first, 0)]
    digest = sha256(POLICY_ID.encode())
    digest.update(initial.token.encode())
    digest.update(eligible.tobytes())
    return eligible, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CertifiedInitialMomentumGrowthPlan:
    initial: CertifiedInitialMomentumPlan
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        expected, token = _selection(self.initial)
        if (type(self.eligible_mask) is not np.ndarray
                or self.eligible_mask.dtype != np.bool_
                or self.eligible_mask.shape != expected.shape
                or not np.array_equal(self.eligible_mask, expected)):
            raise ValueError("First growth eligibility differs from certified anchor selection")
        if self.token != token:
            raise ValueError("First growth content seal differs")
        object.__setattr__(self, "eligible_mask", _frozen(self.eligible_mask))

    @property
    def candidates(self):
        return self.initial.candidates

    @property
    def entry(self):
        return self.initial.entry

    @property
    def momentum(self):
        return self.initial.momentum

    def lookup(self, ticker: str, boundary_ms: int):
        """Materialize the existing anchor only for an admitted sparse row."""
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError("First growth lookup needs exact typed identity")
        index = bisect_left(self.momentum.keys, (ticker, boundary_ms))
        if (index >= len(self.momentum.keys)
                or self.momentum.keys[index] != (ticker, boundary_ms)
                or not self.eligible_mask[index]):
            raise ValueError("First growth lookup is outside admitted candidates")
        anchor = self.initial.lookup(ticker, boundary_ms)
        if not first_setup_momentum_growth_entry(anchor.first_setup):
            raise ValueError("First growth scalar differs from native selection")
        return anchor

    def selection_witness(self, ticker: str, boundary_ms: int):
        return InitialMomentumSelectionWitness(
            self.lookup(ticker, boundary_ms), self.candidates.token,
            self.entry.token, self.token)


def compile_initial_momentum_growth_plan(candidates, entry, momentum):
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    eligible, token = _selection(initial)
    return CertifiedInitialMomentumGrowthPlan(initial, eligible, token)
