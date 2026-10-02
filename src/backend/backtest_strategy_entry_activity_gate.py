"""Prepared survivor reduction with distinct activity rejection reasons.

The wider mask is confined to this new type; historical UInt8 gates and their
content hashes remain unchanged. A gate is not numbered runtime admission.
"""
from dataclasses import dataclass

import numpy as np

from .backtest_strategy_certified_price_break import compile_certified_price_static_gate
from .backtest_strategy_entry_activity_source import CertifiedEntryActivityPlan
from .backtest_strategy_one_static_gate import StrategyOneStaticGate
from .backtest_strategy_rising_momentum import _frozen

ENTRY_ACTIVITY_FADED = 1 << 8
ENTRY_ACTIVITY_MISSING = 1 << 9


def _expected(activity):
    if type(activity) is not CertifiedEntryActivityPlan:
        raise ValueError('Activity gate requires exact certified source plan')
    parent = compile_certified_price_static_gate(activity.parent)
    keys = tuple((fact.ticker, fact.boundary_ms) for fact in parent.facts)
    if keys != activity.parent.momentum.keys:
        raise ValueError('Activity gate facts differ from source candidate clocks')
    reasons = parent.rejection_mask.astype(np.uint16)
    additional = (reasons == 0) & ~activity.eligible_mask
    complete = activity.observed.all(axis=1)
    reasons[additional & complete] |= np.uint16(ENTRY_ACTIVITY_FADED)
    reasons[additional & ~complete] |= np.uint16(ENTRY_ACTIVITY_MISSING)
    return parent.facts, reasons


@dataclass(frozen=True)
class EntryActivityStaticGate(StrategyOneStaticGate):
    """A source-bound UInt16 gate accepted by the shared survivor projection."""
    activity: CertifiedEntryActivityPlan

    def __post_init__(self):
        facts, reasons = _expected(self.activity)
        if (self.facts != facts or type(self.rejection_mask) is not np.ndarray
                or self.rejection_mask.dtype != np.uint16
                or not np.array_equal(self.rejection_mask, reasons)
                or type(self.eligible_indices) is not np.ndarray
                or self.eligible_indices.dtype != np.int64
                or not np.array_equal(self.eligible_indices, np.flatnonzero(reasons == 0))):
            raise ValueError('Activity gate differs from certified parent/source reduction')
        object.__setattr__(self, 'rejection_mask', _frozen(self.rejection_mask))
        object.__setattr__(self, 'eligible_indices', _frozen(self.eligible_indices))


def compile_entry_activity_static_gate(activity):
    facts, reasons = _expected(activity)
    return EntryActivityStaticGate(facts, reasons, np.flatnonzero(reasons == 0).astype(np.int64), activity)
