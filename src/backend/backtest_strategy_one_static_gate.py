"""Vectorized necessary-condition gate for numbered Strategy 1.

STRATEGY CREATION RULES: consume only producer-certified scalar evidence and
reusable rule sets. This mask cannot authorize an order. Permissions, pending
orders, lifecycle, shared cash, and active protection remain sequential OMS
decisions. A changed behavior requires a new published Strategy number.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite

import numpy as np

from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.backend.backtest_strategy_one_activation import CertifiedActivationPlan
from src.backend.backtest_strategy_one_entry_product import CandidateFact
from src.backend.backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from src.backend.backtest_strategy_one_preparation import PreparedStrategyOneTicker


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
        known_bits = (MISSING_FROZEN_GAP | MISSING_COMPLETED_BOS
                      | MISSING_BOS_SUPPORT | MISSING_INITIAL_PROTECTION)
        if (self.rejection_mask.dtype != np.uint8
                or self.eligible_indices.dtype != np.int64
                or self.rejection_mask.shape != (len(self.facts),)
                or self.eligible_indices.ndim != 1
                or np.any(self.rejection_mask & np.uint8(255 ^ known_bits))
                or np.any(self.eligible_indices < 0)
                or np.any(self.eligible_indices >= len(self.facts))
                or not np.array_equal(
                    self.eligible_indices, np.flatnonzero(self.rejection_mask == 0))):
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


def project_static_survivors(
    candidates: CertifiedCandidatePlan, activations: CertifiedActivationPlan,
    gate: StrategyOneStaticGate,
) -> tuple[CertifiedCandidatePlan, CertifiedActivationPlan]:
    """Prune read-only market I/O after full certification, never source facts.

    A rejected boundary has no new entry/add authority. Financially active
    tickers still receive every required broker/management boundary through
    the independent active stream. An episode with no survivors needs no V7
    activation read. The derived tokens are in-memory projection identities,
    not replacements for the producer's full-session coverage seals.
    """
    if (not isinstance(candidates, CertifiedCandidatePlan)
            or not isinstance(activations, CertifiedActivationPlan)
            or not isinstance(gate, StrategyOneStaticGate)
            or len(candidates.token) != 64 or len(activations.token) != 64):
        raise ValueError("Strategy 1 survivor projection lacks sealed inputs")
    if (len({row.ticker for row in candidates.prepared}) != len(candidates.prepared)
            or any(not isinstance(row, PreparedStrategyOneTicker)
                   or len(row.row_index) != len(row.boundary_ms)
                   or len(row.episode_start_ms) != len(row.boundary_ms)
                   or len(row.macd_boundary_ms) != len(row.boundary_ms)
                   or len(row.stop_bar_boundary_ms) != len(row.boundary_ms)
                   or len(row.stop_low_int) != len(row.boundary_ms)
                   or np.any(row.boundary_ms[1:] <= row.boundary_ms[:-1])
                   or np.any(row.episode_start_ms > row.boundary_ms)
                   for row in candidates.prepared)):
        raise ValueError("Strategy 1 survivor source arrays are incomplete")
    expected = tuple((row.ticker, int(boundary), int(start))
                     for row in candidates.prepared
                     for boundary, start in zip(row.boundary_ms,
                                                row.episode_start_ms))
    actual = tuple((fact.ticker, fact.boundary_ms, fact.episode_start_ms)
                   for fact in gate.facts)
    full_starts = {(ticker, start) for ticker, _, start in expected}
    activation_keys = {(row.ticker, row.boundary_ms) for row in activations.rows}
    if (not expected or len(expected) != len(gate.rejection_mask)
            or expected != actual
            or len(activation_keys) != len(activations.rows)
            or activation_keys != full_starts):
        raise ValueError("Strategy 1 static gate differs from candidate/activation plans")
    prepared = []
    offset = 0
    for row in candidates.prepared:
        count = len(row.boundary_ms)
        mask = gate.rejection_mask[offset:offset + count] == 0
        offset += count
        if not np.any(mask):
            continue
        prepared.append(PreparedStrategyOneTicker(
            row.ticker, row.source_rows, row.row_index[mask],
            row.boundary_ms[mask], row.episode_start_ms[mask],
            row.macd_boundary_ms[mask], row.stop_bar_boundary_ms[mask],
            row.stop_low_int[mask]))
    selected_starts = {(row.ticker, int(start)) for row in prepared
                       for start in row.episode_start_ms}
    selected_activations = tuple(row for row in activations.rows
                                 if (row.ticker, row.boundary_ms) in selected_starts)
    if len(selected_activations) != len(selected_starts):
        raise ValueError("Strategy 1 survivor activation is incomplete")
    digest = sha256(b"strategy-one-static-survivors-v1\0")
    digest.update(candidates.token.encode())
    digest.update(activations.token.encode())
    digest.update(gate.rejection_mask.tobytes())
    token = digest.hexdigest()
    return (
        CertifiedCandidatePlan(
            candidates.source_build_id, candidates.candidate_rule_digest,
            candidates.scan_query_sha256, candidates.coverage,
            tuple(prepared), token),
        CertifiedActivationPlan(selected_activations,
                                sha256((activations.token + token).encode()).hexdigest()),
    )
