"""Prepared own-identity admission; no installed actor, order or reservation.

The common scalar financial kernel contributes validated economic values only.
Its legacy proposal identity is never transported, relabelled or submitted.
Portfolio sequencing and cold source-parent admission require a later adapter.
"""
from dataclasses import dataclass
from datetime import date
import json
from math import isfinite
import re
from uuid import UUID, uuid5, NAMESPACE_URL

from .backtest_declared_native_fixed_plan import DeclaredEntrySourcePlan, _policy
from .backtest_market_data import market_day_boundary
from src.trading_runtime.entry_momentum_growth import declared_initial_entry
from src.trading_runtime.entry_spread_risk import exact_epoch_us
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryInput, StrategyOneFinancialView, propose_strategy_one_entry,
)

ENTRY_FAMILY = "declared-native-fixed-entry-preparation@1"


def declared_entry_intent_identity(run_id, strategy_number, strategy_id, revision,
        source_token, assignment_id, account_id, ticker, boundary_ms, episode_start_ms):
    """One deterministic identity recipe; callers validate their own sources."""
    return str(uuid5(NAMESPACE_URL, json.dumps((ENTRY_FAMILY, run_id,
        str(strategy_number), strategy_id, str(revision), source_token,
        assignment_id, account_id, ticker, str(boundary_ms), str(episode_start_ms)),
        separators=(",", ":"))))


@dataclass(frozen=True, slots=True)
class DeclaredNativeFixedEntryProposal:
    run_id: str
    strategy_number: int
    strategy_id: str
    revision: int
    source_token: str
    intent_id: str
    assignment_id: str
    account_id: str
    ticker: str
    boundary_ms: int
    episode_start_ms: int
    reference_ask: float
    initial_stop: float
    initial_target: float
    target_level_id: str
    frozen_gap: float
    bos_break_boundary_ms: int
    bos_support_level_id: str

    def __post_init__(self):
        if (any(type(v) is not int or v <= 0 for v in (self.strategy_number,
                self.revision, self.boundary_ms, self.episode_start_ms, self.bos_break_boundary_ms))
                or self.boundary_ms % 100 or self.episode_start_ms % 100
                or self.bos_break_boundary_ms % 1000
                or not self.bos_break_boundary_ms <= self.boundary_ms
                or not self.episode_start_ms <= self.boundary_ms
                or any(type(v) is not str or not v or len(v) > 256 for v in (
                    self.strategy_id, self.assignment_id, self.account_id, self.ticker,
                    self.target_level_id, self.bos_support_level_id))
                or self.ticker != self.ticker.upper()
                or type(self.source_token) is not str
                or re.fullmatch('[0-9a-f]{64}', self.source_token) is None
                or any(type(v) is not float or not isfinite(v) or v <= 0 for v in (
                    self.reference_ask, self.initial_stop, self.initial_target, self.frozen_gap))):
            raise ValueError("Declared own proposal has invalid typed shape")
        for value in (self.run_id, self.intent_id):
            if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
                raise ValueError("Declared own proposal has invalid canonical identity")

    @property
    def family(self):
        return ENTRY_FAMILY


@dataclass(frozen=True, slots=True)
class DeclaredEntryDecision:
    reason: str
    proposal: DeclaredNativeFixedEntryProposal | None = None


@dataclass(frozen=True, slots=True)
class DeclaredEntryPreparation:
    run_id: str
    assignment_id: str
    account_id: str
    source: DeclaredEntrySourcePlan

    def __post_init__(self):
        if (type(self.run_id) is not str or str(UUID(self.run_id)) != self.run_id
                or not UUID(self.run_id).int or type(self.source) is not DeclaredEntrySourcePlan
                or any(type(s) is not str or not s or len(s) > 256
                       for s in (self.assignment_id, self.account_id))):
            raise ValueError("Declared preparation has invalid own run/account scope")

    def propose(self, ticker, boundary_ms, financial, *, reentry=None):
        """Pure proposal only; caller still owns exact pre-entry ledger authority."""
        if (type(financial) is not StrategyOneFinancialView
                or financial.assignment_id != self.assignment_id
                or financial.account_id != self.account_id or financial.ticker != ticker):
            raise ValueError("Declared financial scope differs")
        parent = self.source.parent
        index = parent.index(ticker, boundary_ms)
        if not self.source.eligible_mask[index]:
            return DeclaredEntryDecision("declared_source_entry_rejected")
        current = parent.momentum.lookup(ticker, boundary_ms)
        if not declared_initial_entry(current, parent.initial(index), _policy(parent.capabilities)):
            raise ValueError("Declared scalar/vector momentum selection differs")
        fact = parent.base.facts[index]
        activations = [a for a in parent.entry.activations
                       if (a.ticker, a.episode_start_ms) == (ticker, fact.episode_start_ms)]
        if len(activations) != 1:
            raise ValueError("Declared original activation is ambiguous or missing")
        bid, ask, quote_us, _ = (int(a[index]) for a in self.source.quote_columns)
        now = exact_epoch_us(market_day_boundary(date.fromisoformat(parent.market.sessions[0]), boundary_ms))
        evidence = StrategyOneEntryInput(ticker, boundary_ms, fact.episode_start_ms,
            activations[0].average_gap, fact.bos_break_boundary_ms, fact.bos_support_level_id,
            fact.protection_valid, fact.stop_price, fact.target_price, fact.target_level_id,
            fact.target_ordinal, bid, ask, now - quote_us, reentry)
        result = propose_strategy_one_entry(evidence, financial)
        if result.proposal is None:
            return DeclaredEntryDecision(result.reason)
        values = result.proposal
        identity = parent.capabilities.identity
        intent = declared_entry_intent_identity(self.run_id, identity.strategy_number,
            identity.strategy_id, identity.revision, self.source.token,
            self.assignment_id, self.account_id, ticker, boundary_ms, fact.episode_start_ms)
        proposal = DeclaredNativeFixedEntryProposal(self.run_id, identity.strategy_number,
            identity.strategy_id, identity.revision, self.source.token, intent,
            values.assignment_id, values.account_id, values.ticker, values.boundary_ms,
            values.episode_start_ms, values.reference_ask, values.initial_stop,
            values.initial_target, values.target_level_id, values.frozen_gap,
            values.bos_break_boundary_ms, values.bos_support_level_id)
        return DeclaredEntryDecision(result.reason, proposal)

    def verify_proposal(self, proposal, financial, *, reentry=None):
        if type(proposal) is not DeclaredNativeFixedEntryProposal:
            raise ValueError("Declared proposal requires exact own family")
        expected = self.propose(proposal.ticker, proposal.boundary_ms, financial, reentry=reentry).proposal
        if expected is None or proposal != expected:
            raise ValueError("Declared proposal differs from scoped causal preparation")
        return proposal
