"""Source-bound Strategy20 selection; numbered runtime admission is separate."""
from bisect import bisect_left
from dataclasses import dataclass
from hashlib import sha256

import numpy as np

from .backtest_strategy_first_price_source import CertifiedFirstPriceSource
from .backtest_strategy_initial_price_break import stage_initial_price_break_plan
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_initial_price_break import (
    FirstSetupPriceBreakWitness, PREMARKET_END_MS, first_setup_price_break,
)
from src.trading_runtime.strategy_initial_strong_momentum import InitialMomentumSelectionWitness


def _selection(source):
    if type(source) is not CertifiedFirstPriceSource:
        raise ValueError('Price selection requires exact certified source')
    staged = stage_initial_price_break_plan(source.parent, source.observations)
    token = sha256((source.token + staged.token).encode()).hexdigest()
    return staged.eligible_mask, token


@dataclass(frozen=True, slots=True)
class CertifiedInitialPriceBreakPlan:
    source: CertifiedFirstPriceSource
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        expected, token = _selection(self.source)
        if (type(self.eligible_mask) is not np.ndarray
                or self.eligible_mask.dtype != np.bool_
                or self.eligible_mask.shape != expected.shape
                or not np.array_equal(self.eligible_mask, expected)):
            raise ValueError('Certified price eligibility differs from source selection')
        if self.token != token:
            raise ValueError('Certified price selection content seal differs')
        object.__setattr__(self, 'eligible_mask', _frozen(self.eligible_mask))

    @property
    def candidates(self):
        return self.source.parent.candidates

    @property
    def entry(self):
        return self.source.parent.entry

    @property
    def momentum(self):
        return self.source.parent.momentum

    def _index(self, ticker, boundary_ms):
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError('Price lookup requires exact typed candidate identity')
        index = bisect_left(self.momentum.keys, (ticker, boundary_ms))
        if (index >= len(self.momentum.keys)
                or self.momentum.keys[index] != (ticker, boundary_ms)
                or not self.eligible_mask[index]):
            raise ValueError('Price lookup is outside admitted candidates')
        return index

    def price_witness(self, ticker, boundary_ms):
        index = self._index(ticker, boundary_ms)
        first = int(self.source.parent.initial.first_indices[index])
        key = self.momentum.keys[first]
        # AH has no extra price rule or source request; absence is explicit.
        if key[1] >= PREMARKET_END_MS:
            return None
        if not self.source.requested_mask[first]:
            raise ValueError('Admitted first setup lacks requested price authority')
        columns = self.source.observations
        witness = FirstSetupPriceBreakWitness(key[0], key[1],
            self.source.market.build_id, self.source.source_attempts[first],
            self.source.market.token, int(columns[0][first]), int(columns[1][first]),
            int(columns[2][first]), int(columns[3][first]),
            bool(columns[4][first]), bool(columns[5][first]))
        if not first_setup_price_break(witness):
            raise ValueError('Scalar price witness differs from native first selection')
        return witness

    def lookup(self, ticker, boundary_ms):
        self._index(ticker, boundary_ms)
        anchor = self.source.parent.lookup(ticker, boundary_ms)
        price = self.price_witness(ticker, boundary_ms)
        if price is not None and price.first_setup_boundary_ms != anchor.first_setup.boundary_ms:
            raise ValueError('Price and momentum first anchors differ')
        return anchor

    def selection_witness(self, ticker, boundary_ms):
        return InitialMomentumSelectionWitness(self.lookup(ticker, boundary_ms),
            self.candidates.token, self.entry.token, self.token)


def compile_certified_price_break_plan(source):
    eligible, token = _selection(source)
    return CertifiedInitialPriceBreakPlan(source, eligible, token)
