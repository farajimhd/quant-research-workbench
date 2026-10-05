"""Source-bound Strategy20 selection; numbered runtime admission is separate."""
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from datetime import date
from types import MappingProxyType
from typing import Mapping
from hashlib import sha256

import numpy as np

from .backtest_strategy_first_price_source import CertifiedFirstPriceSource
from .backtest_strategy_initial_price_break import stage_initial_price_break_plan
from .backtest_strategy_rising_momentum import _frozen
from .backtest_strategy_initial_ten_percent import CertifiedInitialTenPercentPlan
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
    _activations: Mapping = field(init=False, repr=False, compare=False)

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
        activations = {(row.ticker, row.episode_start_ms): row
                       for row in self.source.parent.entry.activations}
        if len(activations) != len(self.source.parent.entry.activations):
            raise ValueError('Certified price source has duplicate activation keys')
        object.__setattr__(self, '_activations', MappingProxyType(activations))

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


def compile_certified_price_static_gate(plan):
    """Reduce the inherited full gate with the source-bound first-price mask.

    Both masks have shape (candidate_count,). The initial-setup rejection bit
    also covers the price rule on that same frozen setup. Portfolio admission
    remains sequential and cannot be authorized by this necessary-condition gate.
    """
    from .backtest_strategy_one_static_gate import (
        compile_static_entry_gate, StrategyOneStaticGate, INITIAL_MOMENTUM_REQUIRED,
    )
    if type(plan) is not CertifiedInitialPriceBreakPlan:
        raise ValueError('Price static gate requires exact certified plan')
    parent = plan.source.parent
    relaxed = type(parent) is CertifiedInitialTenPercentPlan
    inherited = compile_static_entry_gate(plan.candidates, plan.entry,
        strategy_number=18 if relaxed else 19, momentum_plan=plan.momentum,
        initial_momentum_plan=parent.initial if relaxed else parent)
    reasons = inherited.rejection_mask | (
        (~plan.eligible_mask).astype(np.uint8) * INITIAL_MOMENTUM_REQUIRED)
    return StrategyOneStaticGate(inherited.facts, reasons,
        np.flatnonzero(reasons == 0).astype(np.int64))


@dataclass(frozen=True, slots=True)
class CertifiedPriceReadbackAuthority:
    """Reuse one independently compiled source plan across cold journal batches.

    Journal rows supply identity keys only. Prices, attempts, selection and
    content tokens always come from the certified plan, never the companion.
    """
    run_id: str
    plan: CertifiedInitialPriceBreakPlan
    entry_activity_source: object | None = None

    def __post_init__(self):
        if (type(self.run_id) is not str or not self.run_id
                or type(self.plan) is not CertifiedInitialPriceBreakPlan):
            raise ValueError('Price readback requires exact run and certified plan')
        if self.entry_activity_source is not None:
            from .backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
            from .backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
            source = self.entry_activity_source
            if (type(source) not in (EntryActivityReadbackAuthority, EpisodeActivityReadbackAuthority)
                    or source.run_id != self.run_id or source.plan.parent is not self.plan):
                raise ValueError('Price and activity readback require the same certified run and parent plan')

    def resolve(self, run_id, entries, intents):
        from src.trading_runtime.arte_first_price_entry_v4 import FirstPriceEntryAuthority
        if run_id != self.run_id:
            raise ValueError('Price readback differs from certified run')
        parents = {row['record_id']: row for row in intents}
        if len(parents) != len(intents):
            raise ValueError('Price readback has duplicate intent parents')
        result = []
        seen = set()
        month = self.plan.source.market.sessions[0][:7] + '-01'
        for row in entries:
            if row['strategy_number'] not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50):
                continue
            _source_parent_number(self.plan, row['strategy_number'])
            parent = row['parent_record_id']
            intent = parents.get(parent)
            if (type(row['strategy_number']) is not int or parent in seen
                    or intent is None or row['run_id'] != run_id
                    or intent['run_id'] != run_id or str(row['event_month']) != month
                    or intent['batch_id'] != row['batch_id']
                    or intent['action'] != 'enter_long'
                    or intent['reason'] != 'strategy_one_entry'):
                raise ValueError('Price readback has unrelated entry identity')
            key = (intent['ticker'], row['boundary_ms'])
            selection = self.plan.selection_witness(*key)
            if row['episode_start_ms'] != selection.initial.episode_start_ms:
                raise ValueError('Price readback differs from native episode')
            result.append(FirstPriceEntryAuthority(parent,
                self.plan.momentum.lookup(*key), selection,
                self.plan.price_witness(*key), self.plan.source.token, strategy_number=row['strategy_number']))
            seen.add(parent)
        return tuple(result)


def _source_parent_number(plan, strategy_number):
    """Bind the staged number to its exact first-momentum source policy."""
    if (type(plan) is not CertifiedInitialPriceBreakPlan
            or type(strategy_number) is not int
            or strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)):
        raise ValueError('Price binding requires exact certified plan and staged number')
    relaxed = type(plan.source.parent) is CertifiedInitialTenPercentPlan
    if relaxed != (strategy_number in (26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)):
        raise ValueError('Price binding number differs from first-momentum source policy')
    # Both existing scalar factories use the same financial and execution
    # checks. Only 19 adds the 50% first-setup requirement. Nothing is submitted
    # under this internal parent number; the bound intent has its own identity.
    return 18 if relaxed else 19


def bind_certified_price_break_proposal(plan, proposal, *, strategy_number=20):
    """Bind a staged numbered proposal to its exact admitted source policy.

    Portfolio admission remains sequential. Neither this binder nor its source
    compiler changes financial quantities, protection, sizing or execution costs.
    """
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    parent_number = _source_parent_number(plan, strategy_number)
    if (type(strategy_number) is not int or strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)
            or type(plan) is not CertifiedInitialPriceBreakPlan
            or type(proposal) is not StrategyOneEntryProposal
            or proposal.strategy_number != parent_number or type(proposal.strategy_number) is not int):
        raise ValueError('Price proposal binding needs exact certified plan and policy-matched parent proposal')
    parent = plan.source.parent
    key = (proposal.ticker, proposal.boundary_ms)
    if (proposal.momentum != parent.momentum.lookup(*key)
            or proposal.initial_momentum != parent.selection_witness(*key)):
        raise ValueError('Price proposal differs from original parent source selection')
    # Reuse complete scalar intent validation without routing or submitting it.
    strategy_one_entry_intent(proposal, session_date=date.fromisoformat(plan.source.market.sessions[0]))
    selection = plan.selection_witness(*key)
    return replace(proposal, strategy_number=strategy_number, initial_momentum=selection,
                   first_price=plan.price_witness(*key), price_source_token=plan.source.token)


def propose_certified_price_entry(plan, candidate, fact, activation, financial, *, reentry=None,
                                  strategy_number=20):
    """Apply the inherited sequential financial decision to a source-admitted key.

    No price reads or indicator calculations occur per candidate. The cached
    plan owns the original entry facts, first setup and current momentum. A
    financial rejection is returned unchanged, before constructing a proposal.
    """
    from .backtest_strategy_one_stateful import propose_certified_strategy_one_entry
    if (type(plan) is not CertifiedInitialPriceBreakPlan
            or type(strategy_number) is not int or strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)):
        raise ValueError('Price financial decision requires exact certified plan')
    parent_number = _source_parent_number(plan, strategy_number)
    key = (fact.ticker, fact.boundary_ms)
    plan._index(*key)
    if fact != plan.entry.lookup(*key):
        raise ValueError('Price financial decision differs from certified entry facts')
    expected_activation = plan._activations.get((fact.ticker, fact.episode_start_ms))
    if expected_activation is None or activation != expected_activation:
        raise ValueError('Price financial decision differs from certified activation')
    decision = propose_certified_strategy_one_entry(candidate, fact, activation, financial,
        strategy_number=parent_number, momentum=plan.momentum.lookup(*key),
        initial_momentum=plan.source.parent.selection_witness(*key), reentry=reentry)
    if decision.proposal is None:
        return decision
    return replace(decision, proposal=bind_certified_price_break_proposal(plan,
        replace(decision.proposal, strategy_number=parent_number), strategy_number=strategy_number))


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
            or type(proposal.strategy_number) is not int or proposal.strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)):
        raise ValueError('Price projection requires exact certified20 proposal')
    key = (proposal.ticker, proposal.boundary_ms)
    original = replace(proposal, strategy_number=_source_parent_number(plan, proposal.strategy_number), first_price=None, price_source_token=None,
                       initial_momentum=plan.source.parent.selection_witness(*key))
    if bind_certified_price_break_proposal(plan, original,
            strategy_number=proposal.strategy_number) != proposal:
        raise ValueError('Price projection differs from native source-bound proposal')
    if str(event_month) != plan.source.market.sessions[0][:7] + '-01':
        raise ValueError('Price projection month differs from certified market session')
    rows = project_first_price_entry(proposal.momentum, proposal.initial_momentum,
        proposal.first_price, price_source_token=proposal.price_source_token,
        run_id=run_id, batch_id=batch_id, parent_record_id=parent_record_id,
        event_month=event_month, strategy_number=proposal.strategy_number)
    authority = FirstPriceEntryAuthority(parent_record_id, proposal.momentum,
        proposal.initial_momentum, proposal.first_price, proposal.price_source_token, strategy_number=proposal.strategy_number)
    return CertifiedPriceEntryProjection(rows, authority)


def restore_certified_price_proposal(source, proposal, rows, *, parent_record_id, batch_id):
    """Rehydrate a20 scalar entry reference against native and persisted evidence.

    Current and initial momentum must already come from their verified journal
    companions. The native plan independently checks both before restoring the
    price witness. This performs no source read and grants no runtime admission.
    """
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.arte_first_price_entry_v4 import restore_first_price_entry
    from uuid import UUID
    if (type(source) is not CertifiedPriceReadbackAuthority
            or type(proposal) is not StrategyOneEntryProposal
            or type(proposal.strategy_number) is not int or proposal.strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)):
        raise ValueError('Price recovery requires exact certified source and20 reference')
    if proposal.first_price is not None or proposal.price_source_token is not None:
        raise ValueError('Price recovery reference already carries price evidence')
    plan = source.plan
    identities = (parent_record_id, batch_id)
    if any(type(value) is not str or str(UUID(value)) != value or not UUID(value).int for value in identities):
        raise ValueError('Price recovery requires canonical parent and batch identities')
    if any(row['run_id'] != source.run_id or row['parent_record_id'] != parent_record_id
           or row['batch_id'] != batch_id
           or row['strategy_number'] != proposal.strategy_number
           or str(row['event_month']) != plan.source.market.sessions[0][:7] + '-01' for row in rows):
        raise ValueError('Price recovery companion differs from requested run and entry scope')
    key = (proposal.ticker, proposal.boundary_ms)
    selection = plan.selection_witness(*key)
    current = plan.momentum.lookup(*key)
    if proposal.momentum != current or proposal.initial_momentum != selection:
        raise ValueError('Price recovery momentum differs from native source selection')
    price = restore_first_price_entry(rows, current, selection,
        expected_price=plan.price_witness(*key), expected_price_source_token=plan.source.token, strategy_number=proposal.strategy_number)
    recovered = replace(proposal, first_price=price, price_source_token=plan.source.token)
    original = replace(proposal, strategy_number=_source_parent_number(plan, proposal.strategy_number),
        initial_momentum=plan.source.parent.selection_witness(*key))
    if bind_certified_price_break_proposal(plan, original,
            strategy_number=proposal.strategy_number) != recovered:
        raise ValueError('Price recovery proposal differs from complete native binding')
    return recovered


def certified_price_entry_intent(plan, proposal, *, session_date):
    """Construct a staged intent after exact native rebinding of the proposal.

    Financial and execution fields reuse the policy-matched scalar validation.
    Only the numbered deterministic identity changes. Runtime registration and
    publication are separate requirements; this function submits no order.
    """
    from uuid import NAMESPACE_URL, uuid5
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    if (type(plan) is not CertifiedInitialPriceBreakPlan
            or type(proposal) is not StrategyOneEntryProposal
            or type(proposal.strategy_number) is not int or proposal.strategy_number not in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50)
            or type(session_date) is not date
            or session_date.isoformat() != plan.source.market.sessions[0]):
        raise ValueError('Price intent requires exact certified20 proposal and session')
    parent_number = _source_parent_number(plan, proposal.strategy_number)
    original = replace(proposal, strategy_number=parent_number, first_price=None, price_source_token=None,
        initial_momentum=plan.source.parent.selection_witness(proposal.ticker, proposal.boundary_ms))
    if bind_certified_price_break_proposal(plan, original,
            strategy_number=proposal.strategy_number) != proposal:
        raise ValueError('Price intent differs from complete native proposal binding')
    intent = strategy_one_entry_intent(original, session_date=session_date)
    identity = (f'strategy-{proposal.strategy_number}:{session_date.isoformat()}:{proposal.assignment_id}:'
        f'{proposal.account_id}:{proposal.ticker}:{proposal.boundary_ms}:{proposal.episode_start_ms}')
    return replace(intent, intent_id=str(uuid5(NAMESPACE_URL, identity)))
