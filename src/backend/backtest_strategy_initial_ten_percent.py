"""Staged Strategy 26 first-setup policy; no runtime admission is granted.

The existing certified initial compiler selects the earliest structural setup
and requires strict 10% producer histogram growth at that setup and the current
candidate. This separately typed wrapper binds that selection to the proposed
Strategy 26 policy. It does not change the older 50% refinement or authorize a
consumer to bypass it. Price/source certification and numbered approval remain
separate mandatory steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import numpy as np

from .backtest_strategy_initial_momentum import (
    CertifiedInitialMomentumPlan, compile_initial_momentum_plan,
)
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_initial_strong_momentum import InitialMomentumSelectionWitness
from src.trading_runtime.strategy_initial_ten_percent import POLICY_ID, first_setup_ten_percent_entry


def _selection(initial):
    if type(initial) is not CertifiedInitialMomentumPlan:
        raise ValueError('First 10pct policy needs the exact certified initial plan')
    # Shape (N,): the original native compiler already freezes first_indices
    # before momentum/financial pruning and checks both first and current 10%.
    # Reuse its mask unchanged; there is no Python loop over candidates and no
    # opportunity to replace a weak first anchor with a later strong one.
    eligible = initial.eligible_mask
    digest = sha256(POLICY_ID.encode())
    digest.update(initial.token.encode())
    digest.update(eligible.tobytes())
    return eligible, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CertifiedInitialTenPercentPlan:
    initial: CertifiedInitialMomentumPlan
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        expected, token = _selection(self.initial)
        if (type(self.eligible_mask) is not np.ndarray
                or self.eligible_mask.dtype != np.bool_
                or self.eligible_mask.shape != expected.shape
                or not np.array_equal(self.eligible_mask, expected)):
            raise ValueError('First 10pct eligibility differs from certified anchor selection')
        if self.token != token:
            raise ValueError('First 10pct content seal differs')
        object.__setattr__(self, 'eligible_mask', _frozen(self.eligible_mask))

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
        """Reuse scalar parity and original initiality from the native parent."""
        anchor = self.initial.lookup(ticker, boundary_ms)
        if not first_setup_ten_percent_entry(anchor.first_setup):
            raise ValueError('First 10pct scalar differs from native selection')
        return anchor

    def selection_witness(self, ticker: str, boundary_ms: int):
        return InitialMomentumSelectionWitness(
            self.lookup(ticker, boundary_ms), self.candidates.token,
            self.entry.token, self.token)


def compile_initial_ten_percent_plan(candidates, entry, momentum):
    initial = compile_initial_momentum_plan(candidates, entry, momentum)
    eligible, token = _selection(initial)
    return CertifiedInitialTenPercentPlan(initial, eligible, token)
