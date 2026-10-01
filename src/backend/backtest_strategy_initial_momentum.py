"""Sealed first-setup selection over full certified candidate evidence.

This staged compiler does not install or authorize Strategy 18. It selects
anchors before survivor pruning; financial state never participates. Producer
MACD values are reused unchanged from the existing certified momentum plan.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from hashlib import sha256
import re

import numpy as np

from .backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from .backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from .backtest_strategy_one_static_gate import compile_static_entry_gate
from .backtest_strategy_rising_momentum import CertifiedRisingMomentumPlan, _frozen
from src.trading_runtime.strategy_initial_strong_momentum import (
    InitialStrongMomentumWitness, InitialMomentumSelectionWitness, initial_strong_momentum_entry_mask,
    initial_strong_momentum_entry, POLICY_ID,
)


def _selection(candidates, entry, momentum):
    if (type(candidates) is not CertifiedCandidatePlan
            or type(entry) is not CertifiedEntryEvidencePlan
            or type(momentum) is not CertifiedRisingMomentumPlan
            or momentum.source_build_id != candidates.source_build_id
            or momentum.candidate_plan_token != candidates.token
            or any(type(token) is not str or re.fullmatch(r"[0-9a-f]{64}", token) is None
                   for token in (candidates.token, entry.token))):
        raise ValueError("Initial momentum needs exact certified parent plans")
    base = compile_static_entry_gate(candidates, entry, strategy_number=12)
    keys = tuple((fact.ticker, fact.boundary_ms) for fact in base.facts)
    if keys != momentum.keys or np.any((base.rejection_mask == 0) & ~momentum.requested_mask):
        raise ValueError("Initial momentum source omits or changes base candidate keys")
    # Encode strings once; native grouping operates over aligned shape (N,)
    # arrays. Both selection and eligibility retain original candidate indices.
    _, ticker_codes = np.unique(np.asarray([fact.ticker for fact in base.facts]), return_inverse=True)
    starts = np.fromiter((fact.episode_start_ms for fact in base.facts), dtype=np.int64)
    boundaries = np.fromiter((fact.boundary_ms for fact in base.facts), dtype=np.int64)
    first, eligible = initial_strong_momentum_entry_mask(
        ticker_codes.astype(np.int64), starts, boundaries,
        base.rejection_mask == 0, momentum.eligible_mask(17))
    digest = sha256(POLICY_ID.encode())
    for token in (candidates.token, entry.token, momentum.token):
        digest.update(token.encode())
    digest.update(repr(keys).encode())
    for array in (starts, base.rejection_mask, first, eligible):
        digest.update(array.tobytes())
    return starts, first, eligible, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CertifiedInitialMomentumPlan:
    candidates: CertifiedCandidatePlan
    entry: CertifiedEntryEvidencePlan
    momentum: CertifiedRisingMomentumPlan
    episode_start_ms: np.ndarray
    first_indices: np.ndarray
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        expected = _selection(self.candidates, self.entry, self.momentum)
        for name, actual, wanted in zip(
                ("episode_start_ms", "first_indices", "eligible_mask"),
                (self.episode_start_ms, self.first_indices, self.eligible_mask), expected[:3]):
            if (type(actual) is not np.ndarray or actual.dtype != wanted.dtype
                    or actual.shape != wanted.shape or not np.array_equal(actual, wanted)):
                raise ValueError("Initial momentum selection differs from certified first setup")
            object.__setattr__(self, name, _frozen(actual))
        if self.token != expected[3]:
            raise ValueError("Initial momentum content seal differs")

    def lookup(self, ticker: str, boundary_ms: int) -> InitialStrongMomentumWitness:
        """Materialize only an admitted sparse proposal's certified anchor."""
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError("Initial momentum lookup needs exact typed identity")
        index = bisect_left(self.momentum.keys, (ticker, boundary_ms))
        if (index >= len(self.momentum.keys)
                or self.momentum.keys[index] != (ticker, boundary_ms)
                or not self.eligible_mask[index]):
            raise ValueError("Initial momentum lookup is outside admitted candidates")
        first_key = self.momentum.keys[int(self.first_indices[index])]
        anchor = InitialStrongMomentumWitness(
            int(self.episode_start_ms[index]), self.momentum.lookup(*first_key))
        if not initial_strong_momentum_entry(self.momentum.lookup(ticker, boundary_ms), anchor):
            raise ValueError("Initial momentum scalar differs from native selection")
        return anchor

    def selection_witness(self, ticker: str, boundary_ms: int) -> InitialMomentumSelectionWitness:
        return InitialMomentumSelectionWitness(self.lookup(ticker, boundary_ms),
            self.candidates.token, self.entry.token, self.token)


def compile_initial_momentum_plan(candidates, entry, momentum) -> CertifiedInitialMomentumPlan:
    starts, first, eligible, token = _selection(candidates, entry, momentum)
    return CertifiedInitialMomentumPlan(candidates, entry, momentum, starts, first, eligible, token)
