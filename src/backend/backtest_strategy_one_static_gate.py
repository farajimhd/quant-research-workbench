"""Vectorized necessary-condition gate for numbered Strategy 1.

STRATEGY CREATION RULES: consume only producer-certified scalar evidence and
reusable rule sets. This mask cannot authorize an order. Permissions, pending
orders, lifecycle, shared cash, and active protection remain sequential OMS
decisions. A changed behavior requires a new published Strategy number.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np

from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_entry_product import CandidateFact
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan


MISSING_FROZEN_GAP = 1 << 0
MISSING_COMPLETED_BOS = 1 << 1
MISSING_BOS_SUPPORT = 1 << 2
MISSING_INITIAL_PROTECTION = 1 << 3


@dataclass(frozen=True, slots=True)
class StrategyOneStaticGate:
    facts: tuple[CandidateFact, ...]
    rejection_mask: np.ndarray
    eligible_indices: np.ndarray

    def __post_init__(self) -> None:
        if (self.rejection_mask.dtype != np.uint8
                or self.eligible_indices.dtype != np.int64
                or self.rejection_mask.shape != (len(self.facts),)
                or np.any(self.eligible_indices < 0)
                or np.any(self.eligible_indices >= len(self.facts))
                or np.any(self.rejection_mask[self.eligible_indices] != 0)):
            raise ValueError("Strategy 1 static gate arrays differ")
        self.rejection_mask.setflags(write=False)
        self.eligible_indices.setflags(write=False)


def compile_static_entry_gate(
    candidates: CertifiedCandidatePlan, entry: CertifiedEntryEvidencePlan,
) -> StrategyOneStaticGate:
    """Vectorize only position-independent rules over a certified run prefix."""
    if (not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(entry, CertifiedEntryEvidencePlan)
            or candidates.source_build_id != entry.source_build_id
            or not candidates.prepared):
        raise ValueError("Strategy 1 static gate lacks certified candidates")
    activation = {(row.ticker, row.episode_start_ms): row
                  for row in entry.activations}
    if len(activation) != len(entry.activations):
        raise ValueError("Strategy 1 activation evidence is duplicated")
    facts = []
    gaps = []
    for prepared in candidates.prepared:
        for boundary, start in zip(prepared.boundary_ms,
                                   prepared.episode_start_ms):
            fact = entry.lookup(prepared.ticker, int(boundary))
            if fact.episode_start_ms != int(start):
                raise ValueError("Strategy 1 entry fact changed candidate episode")
            frozen = activation.get((prepared.ticker, int(start)))
            if frozen is None:
                raise ValueError("Strategy 1 candidate lacks frozen activation")
            facts.append(fact)
            gaps.append(frozen.average_gap)
    if not facts:
        raise ValueError("Strategy 1 run prefix has no candidate facts")
    # NumPy evaluates all independent candidates in one native mask pass.
    valid_gap = np.fromiter(
        (gap is not None and isfinite(gap) and gap > 0 for gap in gaps),
        dtype=np.bool_, count=len(facts))
    bos = np.fromiter((fact.bos_break_boundary_ms is not None
                       for fact in facts), dtype=np.bool_, count=len(facts))
    support = np.fromiter((bool(fact.bos_support_kind
                                and fact.bos_support_level_id)
                           for fact in facts), dtype=np.bool_, count=len(facts))
    protection = np.fromiter((fact.protection_valid for fact in facts),
                             dtype=np.bool_, count=len(facts))
    reasons = ((~valid_gap).astype(np.uint8) * MISSING_FROZEN_GAP
               | (~bos).astype(np.uint8) * MISSING_COMPLETED_BOS
               | (~support).astype(np.uint8) * MISSING_BOS_SUPPORT
               | (~protection).astype(np.uint8) * MISSING_INITIAL_PROTECTION)
    return StrategyOneStaticGate(
        tuple(facts), reasons, np.flatnonzero(reasons == 0).astype(np.int64))
