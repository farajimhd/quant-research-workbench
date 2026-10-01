"""Staged Strategy20 anchor refinement; this module grants no runtime admission.

The certified producer loader must supply aligned observations. This compiler
binds them to Strategy19's immutable first indices before any financial pruning.
"""
from dataclasses import dataclass
from hashlib import sha256

import numpy as np

from .backtest_strategy_initial_momentum_growth import CertifiedInitialMomentumGrowthPlan
from .backtest_strategy_initial_ten_percent import CertifiedInitialTenPercentPlan
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_initial_price_break import (
    POLICY_ID, first_setup_price_break_mask,
)


def _selection(parent, observations):
    if type(parent) not in (CertifiedInitialMomentumGrowthPlan, CertifiedInitialTenPercentPlan):
        raise ValueError('Price refinement requires an exact certified first-momentum parent')
    if type(observations) is not tuple or len(observations) != 6:
        raise ValueError('Price refinement requires six aligned native producer columns')
    boundaries = np.fromiter((key[1] for key in parent.momentum.keys), dtype=np.int64)
    # All observations have shape (N,). Candidate order is the parent's sealed
    # key order; first indices select the original structural setup, never the
    # first setup remaining after this price comparison or portfolio admission.
    prices = first_setup_price_break_mask(boundaries, *observations)
    first = parent.initial.first_indices
    eligible = parent.eligible_mask & (first >= 0)
    if len(first):
        eligible &= prices[np.maximum(first, 0)]
    digest = sha256(POLICY_ID.encode())
    digest.update(parent.token.encode())
    for column in observations:
        digest.update(column.tobytes())
    digest.update(eligible.tobytes())
    return eligible, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class StagedInitialPriceBreakPlan:
    parent: CertifiedInitialMomentumGrowthPlan | CertifiedInitialTenPercentPlan
    observations: tuple[np.ndarray, ...]
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        expected, token = _selection(self.parent, self.observations)
        if (type(self.eligible_mask) is not np.ndarray
                or self.eligible_mask.dtype != np.bool_
                or self.eligible_mask.shape != expected.shape
                or not np.array_equal(self.eligible_mask, expected)):
            raise ValueError('Price eligibility differs from original anchor selection')
        if self.token != token:
            raise ValueError('Price refinement content seal differs')
        object.__setattr__(self, 'observations', tuple(_frozen(v) for v in self.observations))
        object.__setattr__(self, 'eligible_mask', _frozen(self.eligible_mask))


def stage_initial_price_break_plan(parent, observations):
    """Stage aligned comparisons only; source certification remains mandatory."""
    eligible, token = _selection(parent, observations)
    return StagedInitialPriceBreakPlan(parent, observations, eligible, token)
