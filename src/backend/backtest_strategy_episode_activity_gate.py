"""Prepared source-bound Strategy37 reduction; no numbered admission."""
from dataclasses import dataclass
from bisect import bisect_left
from hashlib import sha256

import numpy as np

from .backtest_strategy_entry_activity_gate import (
    ENTRY_ACTIVITY_FADED, compile_entry_activity_static_gate,
)
from .backtest_strategy_entry_activity_source import CertifiedEntryActivityPlan
from .backtest_strategy_one_static_gate import StrategyOneStaticGate
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_episode_activity_veto import episode_activity_veto_mask

EPISODE_ACTIVITY_VETOED = 1 << 10


def _expected(activity):
    if type(activity) is not CertifiedEntryActivityPlan:
        raise ValueError('Episode activity gate requires exact certified activity source')
    parent = compile_entry_activity_static_gate(activity)
    facts = parent.facts
    # Facts retain the original ticker/episode anchors before survivor pruning.
    # All arrays below are (N,); groups reset on either ticker or episode change.
    tickers = np.asarray([fact.ticker for fact in facts])
    starts = np.fromiter((fact.episode_start_ms for fact in facts), dtype=np.int64)
    if len(starts):
        if np.any((tickers[1:] == tickers[:-1]) & (starts[1:] < starts[:-1])):
            raise ValueError('Episode activity anchors are reordered')
        changed = np.concatenate(([True], (tickers[1:] != tickers[:-1]) | (starts[1:] != starts[:-1])))
        groups = np.cumsum(changed, dtype=np.int64) - 1
    else:
        groups = np.empty(0, dtype=np.int64)
    faded = (parent.rejection_mask & ENTRY_ACTIVITY_FADED) != 0
    # Only a fully observed, otherwise eligible parent decision can latch.
    # Parent rejection and missing activity never count as confirmed failure.
    otherwise_eligible = parent.rejection_mask == 0
    _, vetoed = episode_activity_veto_mask(groups, otherwise_eligible | faded,
                                          otherwise_eligible, faded)
    reasons = parent.rejection_mask.copy()
    reasons[vetoed] |= np.uint16(EPISODE_ACTIVITY_VETOED)
    digest = sha256(('strategy37-episode-activity-gate-v1:' + activity.token).encode())
    for array in (groups, faded, reasons):
        digest.update(array.tobytes())
    return facts, reasons, digest.hexdigest()


@dataclass(frozen=True)
class EpisodeActivityStaticGate(StrategyOneStaticGate):
    activity: CertifiedEntryActivityPlan
    token: str

    def __post_init__(self):
        facts, reasons, token = _expected(self.activity)
        if (self.facts != facts or type(self.rejection_mask) is not np.ndarray
                or self.rejection_mask.dtype != np.uint16
                or not np.array_equal(self.rejection_mask, reasons)
                or type(self.eligible_indices) is not np.ndarray
                or self.eligible_indices.dtype != np.int64
                or not np.array_equal(self.eligible_indices, np.flatnonzero(reasons == 0))
                or self.token != token):
            raise ValueError('Episode activity gate differs from certified causal reduction')
        object.__setattr__(self, 'rejection_mask', _frozen(self.rejection_mask))
        object.__setattr__(self, 'eligible_indices', _frozen(self.eligible_indices))

    def admission_witness(self, ticker, boundary_ms):
        """Return native activity and original anchor for a cached survivor.

        The full prefix has already been sealed during compilation. A sparse
        manager lookup uses binary search only; it neither queries ClickHouse
        nor scans earlier candidates or reconstructs a prefix from journal rows.
        This proof is a necessary entry condition, never portfolio permission.
        """
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError('Episode activity lookup requires exact typed identity')
        keys = self.activity.parent.momentum.keys
        index = bisect_left(keys, (ticker, boundary_ms))
        if (index >= len(keys) or keys[index] != (ticker, boundary_ms)
                or self.rejection_mask[index] != 0):
            raise ValueError('Episode activity lookup is outside admitted causal prefix')
        return (self.activity.witness(ticker, boundary_ms),
                self.facts[index].episode_start_ms, self.token)


def compile_episode_activity_static_gate(activity):
    facts, reasons, token = _expected(activity)
    return EpisodeActivityStaticGate(facts, reasons,
        np.flatnonzero(reasons == 0).astype(np.int64), activity, token)
