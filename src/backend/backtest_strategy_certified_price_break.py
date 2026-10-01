"""Source-bound Strategy20 selection; numbered runtime admission is separate."""
from bisect import bisect_left
from dataclasses import dataclass, replace
from datetime import date
from types import MappingProxyType
from typing import Mapping
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


def bind_certified_price_break_proposal(plan, proposal):
    """Stage a20 proposal from the exact admitted19 source; cannot submit it.

    Portfolio admission remains sequential. Neither this binder nor its source
    compiler changes financial quantities, protection, sizing or execution costs.
    """
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    if (type(plan) is not CertifiedInitialPriceBreakPlan
            or type(proposal) is not StrategyOneEntryProposal
            or proposal.strategy_number != 19 or type(proposal.strategy_number) is not int):
        raise ValueError('Price proposal binding needs exact certified plan and parent19 proposal')
    parent = plan.source.parent
    key = (proposal.ticker, proposal.boundary_ms)
    if (proposal.momentum != parent.momentum.lookup(*key)
            or proposal.initial_momentum != parent.selection_witness(*key)):
        raise ValueError('Price proposal differs from original parent source selection')
    # Reuse complete installed19 scalar intent validation without routing or
    # submitting the resulting value. Number20 remains uninstalled at this stage.
    strategy_one_entry_intent(proposal, session_date=date.fromisoformat(plan.source.market.sessions[0]))
    selection = plan.selection_witness(*key)
    return replace(proposal, strategy_number=20, initial_momentum=selection,
                   first_price=plan.price_witness(*key), price_source_token=plan.source.token)


@dataclass(frozen=True, slots=True)
class CertifiedPriceEntryProjection:
    rows: tuple[Mapping, ...]
    authority: object

    def __post_init__(self):
        from src.trading_runtime.arte_first_price_entry_v4 import FirstPriceEntryAuthority
        if type(self.authority) is not FirstPriceEntryAuthority:
            raise ValueError('Price projection requires exact source authority')
        object.__setattr__(self, 'rows', tuple(MappingProxyType(dict(row)) for row in self.rows))


def project_certified_price_entry(plan, proposal, *, run_id, batch_id,
                                   parent_record_id, event_month):
    """Prepare normalized rows and independent sealer input from one source.

    No writer, registration, DDL or order submission occurs. Journal integration
    must carry both outputs together and retain the source graph checks.
    """
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.arte_first_price_entry_v4 import (
        FirstPriceEntryAuthority, project_first_price_entry,
    )
    if (type(plan) is not CertifiedInitialPriceBreakPlan
            or type(proposal) is not StrategyOneEntryProposal
            or type(proposal.strategy_number) is not int or proposal.strategy_number != 20):
        raise ValueError('Price projection requires exact certified20 proposal')
    key = (proposal.ticker, proposal.boundary_ms)
    original = replace(proposal, strategy_number=19, first_price=None, price_source_token=None,
                       initial_momentum=plan.source.parent.selection_witness(*key))
    if bind_certified_price_break_proposal(plan, original) != proposal:
        raise ValueError('Price projection differs from native source-bound proposal')
    if str(event_month) != plan.source.market.sessions[0][:7] + '-01':
        raise ValueError('Price projection month differs from certified market session')
    rows = project_first_price_entry(proposal.momentum, proposal.initial_momentum,
        proposal.first_price, price_source_token=proposal.price_source_token,
        run_id=run_id, batch_id=batch_id, parent_record_id=parent_record_id,
        event_month=event_month)
    authority = FirstPriceEntryAuthority(parent_record_id, proposal.momentum,
        proposal.initial_momentum, proposal.first_price, proposal.price_source_token)
    return CertifiedPriceEntryProjection(rows, authority)
