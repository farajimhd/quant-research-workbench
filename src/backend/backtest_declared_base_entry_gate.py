"""Columnar base eligibility selected from an exact inherited declaration.

This is a necessary mask over certified producer facts, never an acquisition
authorization. No existing execution path imports this adapter. Native source
and full configuration authority must be established by the owning planner.
"""
from dataclasses import dataclass
from math import isfinite
import re

import numpy as np

from src.trading_runtime.declared_native_fixed_capabilities import DeclaredNativeFixedCapabilities
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.numbered_fixed_strategy import activation_policy_payload, session_policy_payload
from src.trading_runtime.strategy_twelve_release import RECENT_BOS_POLICY
from .backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from .backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from .backtest_strategy_one_static_gate import (
    MISSING_FROZEN_GAP, MISSING_COMPLETED_BOS, MISSING_BOS_SUPPORT,
    MISSING_INITIAL_PROTECTION, SESSION_ACTIVATION_REQUIRED,
    RECENT_BOS_REQUIRED, StrategyOneStaticGate,
)

# Source schema resolution, not a configurable trading threshold. The owning
# certified entry product publishes completed one-second BOS boundaries.
_BOS_SOURCE_RESOLUTION_MS = 1000


def _minute(text):
    if type(text) is not str or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text) is None:
        raise ValueError("Declared base gate requires an exact local clock")
    hour, minute = map(int, text.split(":"))
    return hour * 60 + minute


@dataclass(frozen=True, slots=True)
class DeclaredBaseEntryPolicy:
    """Immutable derived rule parameters; this value grants no execution authority."""
    capabilities: DeclaredNativeFixedCapabilities

    def __post_init__(self):
        if type(self.capabilities) is not DeclaredNativeFixedCapabilities:
            raise ValueError("Declared base gate requires exact inherited capabilities")
        inherited = self.capabilities.payload()["inherited"]
        policies = inherited["policies"]
        # Recognize implemented immutable rule contracts as data. Parent
        # numbers never select a branch, and this adapter cannot change an
        # inherited window or recent-BOS rule under its old contract identity.
        if (canonical_json(policies.get("session_policy")) != canonical_json(session_policy_payload())
                or canonical_json(policies.get("activation_policy")) != canonical_json(activation_policy_payload())
                or canonical_json(policies.get("recent_bos_policy")) != canonical_json(RECENT_BOS_POLICY)):
            raise ValueError("Declared base gate has an unsupported inherited rule contract")
        if self.capabilities.execution_interval != "100ms":
            raise ValueError("Declared base gate differs from certified candidate resolution")

    def parameters(self):
        policies = self.capabilities.payload()["inherited"]["policies"]
        session, activation = policies["session_policy"], policies["activation_policy"]
        matched = re.fullmatch(r"milliseconds_since_(\d\d:\d\d)_(.+)", activation["clock"])
        if matched is None or matched[2] != session["timezone"]:
            raise ValueError("Declared base gate source and session clocks differ")
        origin = _minute(matched[1])
        windows = tuple(tuple((_minute(row[key]) - origin) * 60_000
                              for key in ("start", "entry_cutoff", "end"))
                        for row in session["windows"])
        return windows, policies["recent_bos_policy"]["maximum_bos_age_ms"]


def compile_declared_base_entry_gate(candidates, entry, *, capabilities):
    """Retain deterministic source order; vectorize all independent decisions."""
    policy = DeclaredBaseEntryPolicy(capabilities)
    if (type(candidates) is not CertifiedCandidatePlan
            or type(entry) is not CertifiedEntryEvidencePlan
            or candidates.source_build_id != entry.source_build_id
            or not candidates.prepared or not candidates.coverage
            or any(row.session_date != entry.session_date for row in candidates.coverage)):
        raise ValueError("Declared base gate lacks exact certified source plans")
    activations = {(row.ticker, row.episode_start_ms): row for row in entry.activations}
    if len(activations) != len(entry.activations):
        raise ValueError("Declared base gate activation evidence is duplicated")
    facts, gaps = [], []
    # Gather certified scalars once. No rejected row enters a strategy state
    # machine, and no indicator/structure is derived from market events here.
    for prepared in candidates.prepared:
        if any(type(column) is not np.ndarray or column.dtype != np.int64
               or column.ndim != 1
               for column in (prepared.boundary_ms, prepared.episode_start_ms)):
            raise ValueError("Declared base gate source columns require exact Int64 clocks")
        if len(prepared.boundary_ms) != len(prepared.episode_start_ms):
            raise ValueError("Declared base gate source column cardinality differs")
        for boundary, start in zip(prepared.boundary_ms, prepared.episode_start_ms, strict=True):
            fact = entry.lookup(prepared.ticker, int(boundary))
            if (type(fact.boundary_ms) is not int or type(fact.episode_start_ms) is not int
                    or fact.bos_break_boundary_ms is not None and type(fact.bos_break_boundary_ms) is not int):
                raise ValueError("Declared base gate source clocks require exact integers")
            if fact.episode_start_ms != int(start):
                raise ValueError("Declared base gate changed candidate episode")
            frozen = activations.get((prepared.ticker, int(start)))
            if frozen is None:
                raise ValueError("Declared base gate lacks frozen activation")
            facts.append(fact)
            gaps.append(frozen.average_gap)
    if not facts:
        raise ValueError("Declared base gate prefix has no candidate facts")
    boundaries = np.fromiter((fact.boundary_ms for fact in facts), dtype=np.int64)
    starts = np.fromiter((fact.episode_start_ms for fact in facts), dtype=np.int64)
    breaks = np.fromiter((fact.bos_break_boundary_ms or 0 for fact in facts), dtype=np.int64)
    windows, maximum_age = policy.parameters()
    resolution = int(capabilities.execution_interval[:-2])
    if (np.any(boundaries <= 0) or np.any(boundaries > max(row[2] for row in windows))
            or np.any(boundaries % resolution) or np.any(breaks < 0)
            or np.any(breaks > boundaries) or np.any(breaks % _BOS_SOURCE_RESOLUTION_MS)):
        raise ValueError("Declared base gate needs causal completed source clocks")
    valid_gap = np.fromiter((gap is not None and isfinite(gap) and gap > 0 for gap in gaps),
                            dtype=np.bool_, count=len(facts))
    bos = np.fromiter((fact.bos_break_boundary_ms is not None for fact in facts), dtype=np.bool_)
    support = np.fromiter((bool(fact.bos_support_kind and fact.bos_support_level_id)
                           for fact in facts), dtype=np.bool_)
    protection = np.fromiter((fact.protection_valid for fact in facts), dtype=np.bool_)
    reasons = ((~valid_gap).astype(np.uint8) * MISSING_FROZEN_GAP
               | (~bos).astype(np.uint8) * MISSING_COMPLETED_BOS
               | (~support).astype(np.uint8) * MISSING_BOS_SUPPORT
               | (~protection).astype(np.uint8) * MISSING_INITIAL_PROTECTION)
    same_session = np.zeros(len(facts), dtype=np.bool_)
    for opened, cutoff, _ in windows:
        same_session |= (starts > opened) & (starts <= boundaries) & (boundaries < cutoff)
    recent = (breaks > 0) & ((boundaries - breaks) <= maximum_age)
    reasons |= (~same_session).astype(np.uint8) * SESSION_ACTIVATION_REQUIRED
    reasons |= (~recent).astype(np.uint8) * RECENT_BOS_REQUIRED
    return StrategyOneStaticGate(tuple(facts), reasons,
                                np.flatnonzero(reasons == 0).astype(np.int64))
