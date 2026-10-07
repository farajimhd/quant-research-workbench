"""Position-owned Strategy 1 management on causal completed-bar evidence.

Backtest and a future typed live adapter must use this one numbered rule
implementation. The evidence source supplies completed bars and level inputs;
this module never creates bars, infers intrabucket order, or writes files.
Portfolio/OMS retains sole order authority and confirms protection changes.
"""
from __future__ import annotations
from src.trading_runtime.numbered_fixed_strategy import declared_fixed_rule

from dataclasses import dataclass, replace
from datetime import date
from math import isfinite
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

from src.trading_runtime.strategy_one_management_evidence import (
    StrategyOneManagementEvidence,
)
from src.trading_runtime.strategy_one_add import propose_strategy_one_add
from src.trading_runtime.strategy_one_position import (
    ProtectionState, ResistanceBreak, advance_protection,
    confirm_protection_transition,
)
from src.trading_runtime.strategy_one_stateful import (
    StrategyOneEntryProposal, StrategyOneFinancialView,
)


class StrategyOneManagementEvidenceSource(Protocol):
    """One causal boundary contract, independent of market transport."""

    async def management_evidence(
        self, ticker: str, resolutions: Mapping[int, Mapping], *,
        boundary_ms: int,
    ) -> StrategyOneManagementEvidence: ...


ManagerKey = tuple[str, str, str]  # account, assignment, ticker


@dataclass(frozen=True, slots=True)
class StrategyOneClosedPosition:
    """Causal high of completed bars strictly after a filled entry bucket."""

    closed_boundary_ms: int
    entry_resistance_id: str
    high_int: int


@dataclass(frozen=True, slots=True)
class StrategyOneManagementState:
    """Typed mutable-state capture; never a JSON/disk checkpoint."""

    boundary_ms: int
    submitted: tuple[tuple[ManagerKey, StrategyOneEntryProposal], ...]
    positions: tuple[tuple[ManagerKey, ProtectionState], ...]
    pending_breaks: tuple[tuple[ManagerKey, tuple[ResistanceBreak, ...]], ...]
    position_highs: tuple[tuple[ManagerKey, int], ...] = ()
    closed_positions: tuple[tuple[ManagerKey, StrategyOneClosedPosition], ...] = ()
    first_held_boundaries: tuple[tuple[ManagerKey, int], ...] = ()


@dataclass(frozen=True, slots=True)
class OriginalRiskManagementState(StrategyOneManagementState):
    """Versioned selected capture; legacy state shape stays unchanged."""
    original_risk_requests: tuple = ()


def inherited_management_state_type(state, financial) -> bool:
    """Admit a closed declared capture type without changing inherited facts."""
    if type(state) is StrategyOneManagementState:
        return True
    if (type(state) is not OriginalRiskManagementState
            or type(financial) is not StrategyOneFinancialView
            or type(state.submitted) is not tuple
            or type(state.original_risk_requests) is not tuple
            or any(type(pair) is not tuple or len(pair) != 2 for pair in state.submitted)):
        return False
    key = financial.account_id, financial.assignment_id, financial.ticker
    entries = [source for identity, source in state.submitted if identity == key]
    if (len(entries) != 1 or type(entries[0]) is not StrategyOneEntryProposal
            or type(entries[0].strategy_number) is not int):
        return False
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy
    try:
        policy = numbered_fixed_strategy(entries[0].strategy_number).confirmed_original_risk_policy
    except ValueError:
        return False
    return type(policy) is ConfirmedOriginalRiskPolicy


@dataclass(frozen=True, slots=True)
class LiquidityFadeCheckpointRequest:
    """Completed decision pending an exact native manager/broker checkpoint."""
    witness: Any
    financial: StrategyOneFinancialView
    source_entry_intent_id: str
    observation_source: Mapping[str, Any]

    def __post_init__(self):
        from uuid import UUID
        from src.trading_runtime.strategy_liquidity_fade_exit import validate_liquidity_fade_witness, validate_liquidity_fade_financial
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
        from src.trading_runtime.strategy_half_risk_liquidity_fade import HalfRiskLiquidityFadeFailure
        validate_liquidity_fade_witness(self.witness,
            strategy_number=39 if type(self.witness) is HalfRiskLiquidityFadeFailure else 35)
        validate_liquidity_fade_financial(self.financial)
        if (type(self.source_entry_intent_id) is not str
                or str(UUID(self.source_entry_intent_id)) != self.source_entry_intent_id
                or UUID(self.source_entry_intent_id).int == 0):
            raise ValueError('Liquidity checkpoint request lacks its original entry identity')
        validate_liquidity_observation_source(self.observation_source)
        object.__setattr__(self, 'observation_source', MappingProxyType(dict(self.observation_source)))


class StrategyOneManagementRunner:
    """Keep only active position state; abort on unowned or unconfirmed risk."""

    def __init__(self, *, runtime: Any,
                 evidence: StrategyOneManagementEvidenceSource,
                 tick_for_ticker: Callable[[str], float],
                 max_pending_breaks: int = 256) -> None:
        if (not callable(getattr(runtime, "submit_strategy_one_proposal", None))
                or not callable(getattr(runtime, "submit_strategy_one_add", None))
                or not callable(getattr(runtime, "submit_strategy_one_protection", None))
                or not callable(getattr(evidence, "management_evidence", None))
                or not callable(tick_for_ticker)
                or type(max_pending_breaks) is not int
                or not 1 <= max_pending_breaks <= 65_536):
            raise ValueError("Strategy 1 manager needs bounded OMS and causal inputs")
        self.runtime = runtime
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
        self.contract = numbered_fixed_strategy(
            getattr(getattr(runtime, "config", None), "strategy_revision", 1))
        self.evidence = evidence
        self.tick_for_ticker = tick_for_ticker
        self.max_pending_breaks = max_pending_breaks
        self._submitted: dict[tuple[str, str, str], StrategyOneEntryProposal] = {}
        self._positions: dict[tuple[str, str, str], ProtectionState] = {}
        self._pending_breaks: dict[tuple[str, str, str], list[ResistanceBreak]] = {}
        self._position_highs: dict[ManagerKey, int] = {}
        self._closed_positions: dict[ManagerKey, StrategyOneClosedPosition] = {}
        self._first_held_boundaries: dict[ManagerKey, int] = {}
        # References are selected only after a complete native checkpoint.
        # They are deliberately ephemeral: cold restore must confirm a new
        # checkpoint rather than manufacture a durable reference from highs.
        self._profit_arm_references: dict[ManagerKey, Any] = {}
        self._profit_arm_financials: dict[ManagerKey, StrategyOneFinancialView] = {}
        self._liquidity_lookup = None
        self._liquidity_sources = None
        self._liquidity_requests: dict[ManagerKey, LiquidityFadeCheckpointRequest] = {}
        self._liquidity_latest_five_second: dict[str, Mapping] = {}
        self._completed_risk_lookup = None
        self._original_risk_requests = {}

    def bind_completed_risk_lookup(self,lookup,market_plan):
        from .backtest_confirmed_original_risk_source import CompiledCompletedRiskLookup
        from .backtest_market_data import CertifiedMarketDayPlan
        if (getattr(self.contract, 'confirmed_original_risk_policy', None) is None
                or self._completed_risk_lookup is not None
                or type(lookup) is not CompiledCompletedRiskLookup
                or type(market_plan) is not CertifiedMarketDayPlan
                or lookup.plan != market_plan
                or lookup.session_date != self.runtime.config.anchor_date):
            raise ValueError('Confirmed failure manager needs exact declared prepared source')
        self._completed_risk_lookup = lookup

    async def _submit_followthrough_with_diagnostic(self,financial,witness,source_entry_id,
                                                   *,confirmed=None):
        policy=getattr(self.contract, 'confirmed_original_risk_policy', None)
        if policy is None:
            if confirmed is not None:raise ValueError('Undeclared confirmed witness')
            return await self.runtime.submit_followthrough_failure(financial,witness,source_entry_id)
        if self._completed_risk_lookup is None:
            raise RuntimeError('Declared failure diagnostics lack certified completed source')
        from src.trading_runtime.confirmed_original_risk_failure import (
            OriginalRiskDecisionDiagnostic, INHERITED_ORIGINAL_RISK_RULE,
            CONFIRMED_ORIGINAL_RISK_RULE, validate_decision_diagnostic)
        newest=self._completed_risk_lookup.bucket_at(financial.ticker,witness.boundary_ms)
        if newest is None:raise RuntimeError('Selected failure lacks exact newest producer bucket')
        diagnostic=OriginalRiskDecisionDiagnostic(witness,newest,
            confirmed.prior if confirmed is not None else None,
            CONFIRMED_ORIGINAL_RISK_RULE if confirmed is not None else INHERITED_ORIGINAL_RISK_RULE)
        validate_decision_diagnostic(diagnostic,policy=policy)
        from src.trading_runtime.original_risk_checkpoint import OriginalRiskCheckpointRequest
        request=OriginalRiskCheckpointRequest(diagnostic,financial,source_entry_id)
        key=financial.account_id,financial.assignment_id,financial.ticker
        previous=self._original_risk_requests.get(key)
        if previous is not None and previous != request:
            raise RuntimeError('Original-risk pending checkpoint changed its frozen decision')
        if len(self._original_risk_requests) >= 65536 and key not in self._original_risk_requests:
            raise RuntimeError('Original-risk pending checkpoint inventory exceeds its bound')
        self._original_risk_requests[key]=request

    def original_risk_requests(self, *, boundary_ms):
        requests=tuple(value for key,value in sorted(self._original_risk_requests.items()))
        if any(request.witness.boundary_ms != boundary_ms for request in requests):
            raise RuntimeError('Original-risk decision was not fenced before advancing boundary')
        return requests

    def complete_original_risk_requests(self, requests, *, boundary_ms):
        if requests != self.original_risk_requests(boundary_ms=boundary_ms):
            raise ValueError('Original-risk checkpoint completion changed pending decisions')
        self._original_risk_requests.clear()

    def bind_liquidity_fade_lookup(self, lookup, market_plan) -> None:
        """Bind one independently certified, precompiled source before replay."""
        from .backtest_strategy_liquidity_fade import CompiledLiquidityFadeLookup
        from .backtest_market_data import CertifiedMarketDayPlan
        if ((self.contract.strategy_number not in (35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and not declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-five-completed-liquidity-fade-v1')) or self._liquidity_lookup is not None
                or type(lookup) is not CompiledLiquidityFadeLookup or type(market_plan) is not CertifiedMarketDayPlan
                or lookup.session_date != self.runtime.config.anchor_date
                or ((lookup.strategy_number in (39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(lookup.strategy_number, 'strategy-thirty-nine-half-risk-liquidity-failure-v1'))) != ((self.contract.strategy_number in (39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-nine-half-risk-liquidity-failure-v1')))
                or market_plan.sessions != (lookup.session_date.isoformat(),)
                or lookup.source_build_id != market_plan.build_id or lookup.market_plan_token != market_plan.token
                or not {100, 5000}.issubset(market_plan.required_resolutions_ms)):
            raise ValueError('Liquidity manager lacks its exact prepared session lookup')
        sources = {}
        for ticker in market_plan.tickers:
            refs = dict(source_build_id=market_plan.build_id, source_market_plan_token=market_plan.token)
            for stage, field in (('bars', 'source_bars_attempt_id'), ('technical', 'source_indicators_attempt_id'),
                                 ('broker_100ms', 'source_liquidity_attempt_id')):
                matches = [unit for unit in market_plan.units if unit.ticker == ticker and unit.stage == stage
                           and unit.session_date == market_plan.sessions[0]]
                if len(matches) != 1 or matches[0].build_id != market_plan.build_id:
                    raise ValueError('Liquidity manager has missing or ambiguous pinned producer units')
                refs[field] = matches[0].attempt_id
            from src.trading_runtime.arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
            validate_liquidity_observation_source(refs)
            sources[ticker] = MappingProxyType(refs)
        self._liquidity_lookup, self._liquidity_sources = lookup, MappingProxyType(sources)

    def liquidity_fade_requests(self, *, boundary_ms: int) -> tuple:
        """Return same-boundary requests; none is an order or durable reference."""
        requests = tuple(value for _, value in sorted(self._liquidity_requests.items()))
        if any(value.witness.boundary_ms != boundary_ms for value in requests):
            raise RuntimeError('Liquidity candidate was not fenced before advancing the boundary')
        return requests

    def complete_liquidity_fade_requests(self, requests: tuple, *, boundary_ms: int) -> None:
        if requests != self.liquidity_fade_requests(boundary_ms=boundary_ms):
            raise ValueError('Liquidity checkpoint completion changed its pending decisions')
        self._liquidity_requests.clear()

    @staticmethod
    def _validate_capture(state: StrategyOneManagementState, *,
                          max_pending_breaks: int) -> None:
        if (not isinstance(state, StrategyOneManagementState)
                or type(state.boundary_ms) is not int
                or not 0 <= state.boundary_ms <= 57_600_000
                or state.boundary_ms % 100):
            raise ValueError("Strategy 1 management capture has no causal boundary")
        keys = {}
        for family in ("submitted", "positions", "pending_breaks",
                       "position_highs", "closed_positions", "first_held_boundaries"):
            rows = getattr(state, family)
            identities = [key for key, _ in rows]
            if (any(not isinstance(key, tuple) or len(key) != 3
                           or any(type(part) is not str or not part for part in key)
                           or key[2] != key[2].upper() for key in identities)
                    or identities != sorted(set(identities))):
                raise ValueError("Strategy 1 management capture repeats an identity")
            keys[family] = set(identities)
        if not keys["positions"] <= keys["submitted"] or not keys[
                "pending_breaks"] <= keys["submitted"]:
            raise ValueError("Strategy 1 management state lacks its entry source")
        if keys["position_highs"] != keys["positions"]:
            raise ValueError("Strategy 1 position high lacks its active position")
        sources = dict(state.submitted)
        required = {key for key in keys["positions"]
                    if (sources[key].strategy_number in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(sources[key].strategy_number, 'strategy-nine-followthrough-failure-v1'))}
        if keys["first_held_boundaries"] != required:
            raise ValueError("Strategy 9 position lacks its first held boundary")
        for key, boundary in state.first_held_boundaries:
            if (type(boundary) is not int or boundary % 100
                    or not sources[key].boundary_ms < boundary <= state.boundary_ms):
                raise ValueError("Strategy 9 first held boundary is not causal")
        for key, proposal in state.submitted:
            if (not isinstance(proposal, StrategyOneEntryProposal)
                    or (proposal.account_id, proposal.assignment_id,
                        proposal.ticker) != key
                    or proposal.boundary_ms > state.boundary_ms):
                raise ValueError("Strategy 1 submitted entry differs from capture")
        for _, position in state.positions:
            if (not isinstance(position, ProtectionState)
                    or position.boundary_ms > state.boundary_ms):
                raise ValueError("Strategy 1 position is ahead of capture")
        for _, breaks in state.pending_breaks:
            if (not isinstance(breaks, tuple) or len(breaks) > max_pending_breaks
                    or any(not isinstance(row, ResistanceBreak)
                           or not isinstance(row.level, Mapping)
                           or set(row.level) != {
                               "unified_level_id", "lower", "upper", "role", "side"}
                           or type(row.level["unified_level_id"]) is not str
                           or not row.level["unified_level_id"]
                           or row.level["role"] != "resistance"
                           or row.level["side"] != "resistance"
                           or type(row.level["lower"]) not in (int, float)
                           or type(row.level["upper"]) not in (int, float)
                           or not isfinite(row.level["lower"])
                           or not isfinite(row.level["upper"])
                           or not 0 < row.level["lower"] <= row.level["upper"]
                           or row.completed_boundary_ms > state.boundary_ms
                           for row in breaks)):
                raise ValueError("Strategy 1 pending break is ahead of capture")
        for _, high_int in state.position_highs:
            if type(high_int) is not int or high_int <= 0:
                raise ValueError("Strategy 1 position high is invalid")
        for _, prior in state.closed_positions:
            if (not isinstance(prior, StrategyOneClosedPosition)
                    or type(prior.closed_boundary_ms) is not int
                    or not 0 < prior.closed_boundary_ms <= state.boundary_ms
                    or prior.closed_boundary_ms % 100
                    or not prior.entry_resistance_id
                    or type(prior.high_int) is not int or prior.high_int <= 0):
                raise ValueError("Strategy 1 closed position witness is invalid")

    def capture_state(self, *, boundary_ms: int) -> StrategyOneManagementState:
        """Capture only position-owned facts at an ordered global boundary."""
        state = StrategyOneManagementState(
            boundary_ms,
            tuple(sorted(self._submitted.items())),
            tuple(sorted(self._positions.items())),
            tuple(sorted((key, tuple(ResistanceBreak(
                row.completed_boundary_ms, MappingProxyType(dict(row.level)))
                for row in value)) for key, value in
                         self._pending_breaks.items())),
            tuple(sorted(self._position_highs.items())),
            tuple(sorted(self._closed_positions.items())),
            tuple(sorted(self._first_held_boundaries.items())),
        )
        self._validate_capture(state, max_pending_breaks=self.max_pending_breaks)
        if getattr(self.contract,'confirmed_original_risk_policy',None) is not None:
            from dataclasses import fields
            state=OriginalRiskManagementState(**{f.name:getattr(state,f.name)
                for f in fields(StrategyOneManagementState)},
                original_risk_requests=self.original_risk_requests(boundary_ms=boundary_ms))
        return state

    def restore_state(self, state: StrategyOneManagementState, *, first_price_source=None) -> None:
        """Cold typed restore only; a populated manager cannot be overwritten."""
        if (self._submitted or self._positions or self._pending_breaks
                or self._position_highs or self._closed_positions
                or self._first_held_boundaries):
            raise RuntimeError("Strategy 1 manager is already active")
        self._validate_capture(state, max_pending_breaks=self.max_pending_breaks)
        from src.trading_runtime.strategy_rising_momentum_witness import numbered_momentum_entry
        for _, proposal in state.submitted:
            if (proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(proposal.strategy_number, 'strategy-twenty-premarket-first-completed-one-second-price-break-v1')):
                from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority, certified_price_entry_intent
                if (type(first_price_source) is not CertifiedPriceReadbackAuthority
                        or first_price_source.run_id != getattr(self.runtime, 'run_id', None)):
                    raise ValueError("Strategy20 manager recovery lacks its native source context")
                certified_price_entry_intent(first_price_source.plan, proposal,
                    session_date=date.fromisoformat(first_price_source.plan.source.market.sessions[0]))
                if (proposal.strategy_number in (37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(proposal.strategy_number, 'strategy-thirty-seven-confirmed-episode-activity-veto-v1')):
                    from .backtest_strategy_episode_activity_source import certified_episode_activity_witness
                    certified_episode_activity_witness(first_price_source, proposal,
                        session_date=date.fromisoformat(first_price_source.plan.source.market.sessions[0]))
                elif proposal.strategy_number == 36:
                    from .backtest_strategy_entry_activity_source import certified_entry_activity_witness
                    certified_entry_activity_witness(first_price_source, proposal,
                        session_date=date.fromisoformat(first_price_source.plan.source.market.sessions[0]))
            if (proposal.strategy_number in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(proposal.strategy_number, 'strategy-thirteen-rising-completed-momentum-v1')) and (
                    not numbered_momentum_entry(proposal.momentum, proposal.strategy_number)
                    or proposal.momentum.ticker != proposal.ticker
                    or proposal.momentum.boundary_ms != proposal.boundary_ms):
                raise ValueError("Strategy 13 manager recovery lacks its committed momentum source")
            if (proposal.strategy_number in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(proposal.strategy_number, 'strategy-eighteen-first-strong-momentum-setup-v1')):
                from src.trading_runtime.strategy_initial_strong_momentum import (
                    validate_initial_momentum_selection, initial_strong_momentum_entry,
                )
                validate_initial_momentum_selection(proposal.momentum, proposal.initial_momentum,
                                                   episode_start_ms=proposal.episode_start_ms)
                if not initial_strong_momentum_entry(proposal.momentum, proposal.initial_momentum.initial):
                    raise ValueError("Strategy 18 manager recovery lacks strong first-setup source")
                if proposal.strategy_number in (19, 20, 21, 22, 23, 24, 25):
                    from src.trading_runtime.strategy_initial_momentum_growth import first_setup_momentum_growth_entry
                    if not first_setup_momentum_growth_entry(proposal.initial_momentum.initial.first_setup):
                        raise ValueError("Strategy 19 manager recovery requires premarket first-setup 50pct growth")
            elif proposal.initial_momentum is not None:
                raise ValueError("Earlier manager cannot restore initial momentum selection")
        pending_requests={}
        selected=getattr(self.contract,'confirmed_original_risk_policy',None)
        if selected is not None:
            if type(state) is not OriginalRiskManagementState:
                raise ValueError('Selected manager recovery lacks durable pending-decision authority')
            requests=state.original_risk_requests
            from src.trading_runtime.original_risk_checkpoint import validate_original_risk_state
            for request in requests:
                validate_original_risk_state(request.witness,state,request.financial)
            pending_requests={
                (request.financial.account_id,request.financial.assignment_id,request.financial.ticker):request
                for request in requests}
            if len(pending_requests)!=len(requests):
                raise ValueError('Recovered original-risk pending decisions repeat identity')
        elif type(state) is OriginalRiskManagementState:
            raise ValueError('Legacy manager cannot restore selected pending decisions')
        self._submitted = dict(state.submitted)
        self._positions = dict(state.positions)
        self._pending_breaks = {key: [ResistanceBreak(
            row.completed_boundary_ms, MappingProxyType(dict(row.level)))
            for row in rows] for key, rows in state.pending_breaks}
        self._position_highs = dict(state.position_highs)
        self._closed_positions = dict(state.closed_positions)
        self._first_held_boundaries = dict(state.first_held_boundaries)
        if selected is not None:
            self._original_risk_requests=pending_requests

    def owns_position_source(self, financial: StrategyOneFinancialView) -> bool:
        """Check ownership before cleanup; a same-bucket exit cannot reenter."""
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 ownership needs typed financial state")
        return ((financial.account_id, financial.assignment_id, financial.ticker)
                in self._submitted)

    def profit_arming_requests(self, *, boundary_ms: int) -> tuple:
        """Freeze all newly armed positions against one completed capture."""
        if (self.contract.strategy_number not in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) and not declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-one-original-risk-profit-giveback-v1')):
            return ()
        from decimal import Decimal
        from src.trading_runtime.strategy_profit_giveback_arm import profit_arm_candidate
        eligible = []
        for key, financial in sorted(self._profit_arm_financials.items()):
            if (key in self._profit_arm_references or key not in self._positions
                    or financial.position_quantity <= 0 or financial.pending_exit):
                continue
            source = self._submitted[key]
            threshold = (2 * Decimal(str(source.reference_ask))
                         - Decimal(str(source.initial_stop))) * 10_000
            if Decimal(self._position_highs[key]) >= threshold:
                eligible.append(financial)
        if not eligible:
            # Deep checkpoint capture is paid once per armed position, not
            # at every 100ms market boundary while waiting for follow-through.
            return ()
        state = self.capture_state(boundary_ms=boundary_ms)
        requests = []
        for financial in eligible:
            candidate = profit_arm_candidate(state, financial,
                already_checkpointed=False)
            if candidate is not None:
                requests.append((candidate, financial))
        return tuple(requests)

    def accept_profit_arming_references(self, requests: tuple, references: tuple,
                                       *, boundary_ms: int) -> None:
        """Retain confirmed references atomically before advancing the clock."""
        from src.trading_runtime.strategy_profit_giveback_arm_reference import ProfitArmReference
        if (type(requests) is not tuple or type(references) is not tuple
                or not requests or len(requests) != len(references)
                or requests != self.profit_arming_requests(boundary_ms=boundary_ms)
                or any(type(reference) is not ProfitArmReference
                       or reference.candidate != candidate
                       for (candidate, _), reference in zip(requests, references))):
            raise ValueError('Profit arming confirmation differs from completed positions')
        for reference in references:
            candidate = reference.candidate
            key = (candidate.account_id, candidate.assignment_id, candidate.ticker)
            self._profit_arm_references[key] = reference

    def last_closed_position(
        self, financial: StrategyOneFinancialView,
    ) -> StrategyOneClosedPosition | None:
        """Expose only a completed, checkpointed prior position to the reducer."""
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 prior position needs typed financial state")
        return self._closed_positions.get((
            financial.account_id, financial.assignment_id, financial.ticker))

    async def on_entry_proposal(self, proposal: StrategyOneEntryProposal) -> None:
        if not isinstance(proposal, StrategyOneEntryProposal):
            raise TypeError("Strategy 1 manager needs a numbered entry proposal")
        if (proposal.strategy_number != self.contract.strategy_number
                or not self.contract.entry_allowed(proposal.boundary_ms)):
            raise ValueError("Numbered entry crossed its strategy/session contract")
        key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
        if key in self._submitted:
            raise RuntimeError("Strategy 1 assignment already owns an entry")
        results = await self.runtime.submit_strategy_one_proposal(proposal)
        if (len(results) != 1 or results[0].get("order_group") is None
                or results[0].get("decision", {}).get("status")
                not in {"approved", "resized"}):
            return
        self._submitted[key] = proposal

    async def on_management(
        self, financial: StrategyOneFinancialView,
        resolutions: Mapping[int, Mapping], boundary_ms: int,
    ) -> None:
        if self._original_risk_requests:
            self.original_risk_requests(boundary_ms=boundary_ms)
        if not isinstance(financial, StrategyOneFinancialView):
            raise TypeError("Strategy 1 management needs typed financial state")
        key = (financial.account_id, financial.assignment_id, financial.ticker)
        if (self.contract.strategy_number in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-one-original-risk-profit-giveback-v1')):
            self._profit_arm_financials[key] = financial
        if financial.position_quantity <= 0:
            if not financial.pending_entry and not financial.pending_exit:
                source = self._submitted.get(key)
                high_int = self._position_highs.pop(key, None)
                if source is not None and high_int is not None:
                    self._closed_positions[key] = StrategyOneClosedPosition(
                        boundary_ms, source.bos_support_level_id, high_int)
                self._positions.pop(key, None)
                self._first_held_boundaries.pop(key, None)
                self._pending_breaks.pop(key, None)
                self._submitted.pop(key, None)
                self._profit_arm_references.pop(key, None)
                self._profit_arm_financials.pop(key, None)
            return
        if self.contract.liquidation_due(boundary_ms):
            await self.runtime.submit_numbered_session_exit(financial, resolutions, boundary_ms)
            return
        source = self._submitted.get(key)
        if source is None:
            raise RuntimeError("Strategy 1 position lacks its normalized entry source")
        if key not in self._positions:
            if boundary_ms < source.boundary_ms:
                raise ValueError("Strategy 1 fill precedes its source entry")
            # The first observed held boundary owns the position. A 1s break
            # at that same boundary cannot be ordered after the fill inside
            # its aggregate bucket, so it cannot advance protection yet.
            self._positions[key] = ProtectionState(
                boundary_ms, source.initial_stop, source.initial_target)
            # The fill can occur anywhere inside its aggregate liquidity bar.
            # Do not include that bucket's high in the prior-position witness.
            self._position_highs[key] = round(source.reference_ask * 10_000)
            if self.contract.allows_followthrough_failure_exit:
                self._first_held_boundaries[key] = boundary_ms
            return
        previous = self._positions[key]
        if boundary_ms <= previous.boundary_ms:
            raise ValueError("Strategy 1 position management clock did not advance")
        current_bar = resolutions.get(100)
        if current_bar is not None and current_bar.get("price_valid") == 1:
            high_int = current_bar.get("high_int")
            if type(high_int) is not int or high_int <= 0:
                raise ValueError("Strategy 1 held bar lacks certified high")
            self._position_highs[key] = max(
                self._position_highs[key], high_int)
        evidence = await self.evidence.management_evidence(
            financial.ticker, resolutions, boundary_ms=boundary_ms)
        if (type(evidence) is not StrategyOneManagementEvidence
                or evidence.ticker != financial.ticker
                or evidence.boundary_ms != boundary_ms):
            raise ValueError("Strategy 1 management evidence crossed its causal boundary")
        if self.contract.allows_followthrough_failure_exit and boundary_ms % 5_000 == 0:
            from src.trading_runtime.strategy_followthrough_failure import (
                FollowThroughFailureInput, followthrough_failure,
            )
            from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
            from src.backend.backtest_market_data import market_day_boundary
            bar = resolutions.get(5_000) or {}
            quote = resolutions.get(100) or {}
            at = market_day_boundary(self.runtime.config.anchor_date, boundary_ms)
            quote_us = quote.get("quote_timestamp_us")
            age_us = (int(at.timestamp() * 1_000_000) - quote_us
                      if type(quote_us) is int and quote.get("quote_valid") == 1
                      else None)
            from src.trading_runtime.strategy_early_followthrough_failure import early_followthrough_failure
            # The installed number owns eligibility; old seals retain the
            # original unbounded predicate and first-held checkpoint contract.
            from src.trading_runtime.strategy_premarket_quarter_risk_failure import premarket_quarter_risk_failure
            from src.trading_runtime.strategy_persistent_risk_failure import persistent_risk_failure
            from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure
            failure_rule = (zero_regime_risk_failure
                            if (self.contract.strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-zero-regime-original-risk-failure-v1'))
                            else persistent_risk_failure
                            if self.contract.strategy_number == 29
                            else premarket_quarter_risk_failure
                            if self.contract.strategy_number in (25, 26, 27, 28)
                            else early_followthrough_failure
                            if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24)
                            else followthrough_failure)
            completed = FollowThroughFailureInput(
                boundary_ms, self._first_held_boundaries[key],
                source.reference_ask, source.initial_stop,
                bar.get("boundary_ms"), bar.get("close_int"),
                bar.get("price_valid") == 1, bar.get("macd_line"),
                bar.get("macd_signal"), evidence.bid, evidence.ask, age_us,
                financial.position_quantity, financial.pending_exit)
            witness = failure_rule(completed)
            if witness is not None:
                # Reuse the runtime's cached, exact native source validation;
                # the older constructor deliberately excludes price entries.
                entry = (self.runtime._strategy_one_entry_intent(source)
                         if (self.contract.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-twenty-premarket-first-completed-one-second-price-break-v1'))
                         else strategy_one_entry_intent(
                             source, session_date=self.runtime.config.anchor_date))
                await self._submit_followthrough_with_diagnostic(
                    financial, witness, entry.intent_id)
                if (self.contract.strategy_number in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-one-original-risk-profit-giveback-v1')):
                    # The pre-submission financial view cannot attest that
                    # the position is still available for arming after OMS.
                    # Refresh it on a later management boundary if held.
                    self._profit_arm_financials.pop(key, None)
                return
            if (self.contract.strategy_number in (31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-one-original-risk-profit-giveback-v1')):
                from src.trading_runtime.strategy_profit_giveback import ProfitGivebackInput, profit_giveback
                reference = self._profit_arm_references.get(key)
                # Confirmation belongs to finish(), so even an arm selected
                # on this boundary cannot authorize an exit inside it.
                if reference is not None and reference.candidate.boundary_ms < boundary_ms:
                    profit_witness = profit_giveback(ProfitGivebackInput(
                        completed, reference.candidate.high_int,
                        reference.candidate.boundary_ms))
                    if profit_witness is not None:
                        entry = self.runtime._strategy_one_entry_intent(source)
                        await self.runtime.submit_profit_giveback(
                            financial, profit_witness, entry.intent_id, reference)
                        return
            if (self.contract.strategy_number in (34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy.confirmed-ah-risk-failure.v1')):
                from src.trading_runtime.strategy_confirmed_ah_risk_failure import (
                    ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure,
                )
                # Reuse the completed producer frame; no additional reads or
                # candle construction occur in the management loop. Inherited
                # failure and armed-profit exits above retain first priority.
                ten = resolutions.get(10_000) or {}
                confirmed_ah = confirmed_ah_risk_failure(ConfirmedAhRiskFailureInput(
                    completed, ten.get("boundary_ms"), ten.get("price_valid") == 1,
                    ten.get("macd_line"), ten.get("macd_signal")))
                if confirmed_ah is not None:
                    entry = self.runtime._strategy_one_entry_intent(source)
                    await self.runtime.submit_confirmed_ah_failure(
                        financial, confirmed_ah, entry.intent_id)
                    self._profit_arm_financials.pop(key, None)
                    return
        if (self.contract.strategy_number in (35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-five-completed-liquidity-fade-v1')):
            if self._liquidity_lookup is None:
                raise RuntimeError('Strategy 35 requires its precompiled liquidity source')
            from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeInput
            from src.trading_runtime.strategy_half_risk_liquidity_fade import numbered_liquidity_fade_failure
            from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
            from src.backend.backtest_market_data import market_day_boundary
            completed_bar = resolutions.get(5000)
            if completed_bar is not None:
                completed_at = completed_bar.get('boundary_ms')
                if (type(completed_at) is not int or completed_at % 5000
                        or not 0 < completed_at <= boundary_ms):
                    raise ValueError('Liquidity manager received a forming or invalid completed bar')
                self._liquidity_latest_five_second[financial.ticker] = MappingProxyType({
                    name: completed_bar.get(name) for name in
                    ('boundary_ms', 'close_int', 'price_valid', 'macd_line', 'macd_signal')})
            window = self._liquidity_lookup.window_at(financial.ticker, boundary_ms)
            if window is not None:
                bar = self._liquidity_latest_five_second.get(financial.ticker) or {}
                quote = resolutions.get(100) or {}
                at = market_day_boundary(self.runtime.config.anchor_date, boundary_ms)
                from datetime import datetime, timezone
                elapsed = at.astimezone(timezone.utc)-datetime(1970, 1, 1, tzinfo=timezone.utc)
                now_us = (elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
                quote_us = quote.get('quote_timestamp_us')
                age_us = (now_us-quote_us
                          if type(quote_us) is int and quote.get('quote_valid') == 1 else None)
                candidate = numbered_liquidity_fade_failure(LiquidityFadeInput(FollowThroughFailureInput(
                    boundary_ms, self._first_held_boundaries[key], source.reference_ask, source.initial_stop,
                    bar.get('boundary_ms'), bar.get('close_int'), bar.get('price_valid') == 1,
                    bar.get('macd_line'), bar.get('macd_signal'), evidence.bid, evidence.ask, age_us,
                    financial.position_quantity, financial.pending_exit), window.candles),
                    strategy_number=self.contract.strategy_number)
                if candidate is not None:
                    entry = self.runtime._strategy_one_entry_intent(source)
                    request = LiquidityFadeCheckpointRequest(candidate, financial, entry.intent_id,
                                                            self._liquidity_sources[financial.ticker])
                    previous_request = self._liquidity_requests.get(key)
                    if previous_request is not None and previous_request != request:
                        raise RuntimeError('Liquidity checkpoint retry changed its completed decision')
                    if len(self._liquidity_requests) >= 65_536 and key not in self._liquidity_requests:
                        raise RuntimeError('Liquidity checkpoint candidates exceed the bounded assignment inventory')
                    self._liquidity_requests[key] = request
                    self._profit_arm_financials.pop(key, None)
                    return
        # A new release can declare an additional pure failure rule. Every
        # inherited exit above, including deferred liquidity confirmation,
        # retains its original priority. Older contracts declare no extension.
        early_policy = getattr(self.contract, 'early_original_risk_policy', None)
        if early_policy is not None and boundary_ms % 5_000 == 0:
            from src.trading_runtime.early_original_risk_failure import early_original_risk_failure
            witness = early_original_risk_failure(completed, policy=early_policy)
            if witness is not None:
                entry = self.runtime._strategy_one_entry_intent(source)
                await self.runtime.submit_followthrough_failure(financial, witness, entry.intent_id)
                self._profit_arm_financials.pop(key, None)
                return
        profit_policy = getattr(self.contract, 'armed_profit_floor_policy', None)
        if profit_policy is not None and boundary_ms % 5_000 == 0:
            from src.trading_runtime.declared_profit_giveback import armed_profit_floor
            from src.trading_runtime.strategy_profit_giveback import ProfitGivebackInput
            reference = self._profit_arm_references.get(key)
            if reference is not None and reference.candidate.boundary_ms < boundary_ms:
                witness = armed_profit_floor(ProfitGivebackInput(
                    completed, reference.candidate.high_int, reference.candidate.boundary_ms),
                    policy=profit_policy)
                if witness is not None:
                    entry = self.runtime._strategy_one_entry_intent(source)
                    await self.runtime.submit_profit_giveback(
                        financial, witness, entry.intent_id, reference)
                    return
        all_held_policy = getattr(self.contract, 'all_held_original_risk_policy', None)
        if all_held_policy is not None and boundary_ms % 5_000 == 0:
            from src.trading_runtime.all_held_original_risk_failure import all_held_original_risk_failure
            witness = all_held_original_risk_failure(completed, policy=all_held_policy)
            if witness is not None:
                entry = self.runtime._strategy_one_entry_intent(source)
                await self.runtime.submit_followthrough_failure(financial, witness, entry.intent_id)
                self._profit_arm_financials.pop(key, None)
                return
        confirmed_policy = getattr(self.contract, 'confirmed_original_risk_policy', None)
        if confirmed_policy is not None and boundary_ms % 5000 == 0:
            if self._completed_risk_lookup is None:
                raise RuntimeError('Declared consecutive failure lacks certified completed source')
            pair=self._completed_risk_lookup.pair_at(financial.ticker,boundary_ms)
            if pair is not None:
                from src.trading_runtime.confirmed_original_risk_failure import confirmed_original_risk_failure
                confirmation=confirmed_original_risk_failure(completed,prior=pair[0],newest=pair[1],
                                                            policy=confirmed_policy)
                if confirmation is not None:
                    entry=self.runtime._strategy_one_entry_intent(source)
                    await self._submit_followthrough_with_diagnostic(financial,confirmation.current,
                        entry.intent_id,confirmed=confirmation)
                    self._profit_arm_financials.pop(key,None)
                    return
        pending = self._pending_breaks.setdefault(key, [])
        # A failed OMS acknowledgement retries the same completed boundary.
        # Preserve witnesses once, not once per retry.
        seen = {(row.completed_boundary_ms,
                 row.level.get("unified_level_id")) for row in pending}
        additions = [row for row in evidence.breaks
                     if (row.completed_boundary_ms,
                         row.level.get("unified_level_id")) not in seen]
        if len(pending) + len(additions) > self.max_pending_breaks:
            raise RuntimeError("Strategy 1 pending resistance witnesses exceed memory bound")
        pending.extend(additions)
        if evidence.bid is None or evidence.ask is None or financial.pending_exit:
            return
        tick = self.tick_for_ticker(financial.ticker)
        if type(tick) not in (int, float) or not isfinite(tick) or tick <= 0:
            raise ValueError("Strategy 1 management lacks a point-in-time tick")
        transition = advance_protection(
            previous, now_ms=boundary_ms, bid=evidence.bid, ask=evidence.ask,
            tick=tick, low_boundary_ms=evidence.low_boundary_ms,
            low_int=evidence.low_int,
            low_price_valid=evidence.low_int is not None,
            low_extremes_valid=evidence.low_int is not None,
            breaks=tuple(pending), overhead_levels=evidence.overhead_levels,
            price_bearing_bar=evidence.price_bearing_bar,
            allows_completed_30s_trailing=self.contract.allows_completed_30s_trailing,
            allows_target_escalation=self.contract.allows_target_escalation)
        if (transition.stop_amendment is None
                and transition.target_amendment is None):
            # No broker command exists to acknowledge. Advance the completed
            # causal clock through the same pure confirmation used by the
            # runtime, without an empty OMS coroutine on every 100 ms row.
            confirmed = confirm_protection_transition(
                previous, transition, target_confirmed=False,
                stop_confirmed=False)
        else:
            confirmed = await self.runtime.submit_strategy_one_protection(
                previous, transition, financial, bid=evidence.bid, ask=evidence.ask)
        if not isinstance(confirmed, ProtectionState):
            raise RuntimeError("Strategy 1 OMS did not return confirmed protection")
        if (confirmed.boundary_ms != boundary_ms
                or not previous.accepted_ids <= confirmed.accepted_ids
                or not 0 < confirmed.stop < confirmed.target):
            raise RuntimeError("Strategy 1 OMS returned inconsistent protection")
        self._positions[key] = confirmed
        pending.clear()
        # This 1s boundary and its co-terminating 100ms row are both closed.
        # Resistances become actionable only after protection has been
        # acknowledged. A rejection consumes this crossing, not a future one.
        if (not self.contract.allows_adds
                or financial.pending_entry or financial.current_purchase_groups >= 3
                or not self.contract.entry_allowed(boundary_ms)):
            return
        purchase_ordinal = financial.current_purchase_groups + 1
        for resistance in sorted(
                evidence.breaks,
                key=lambda row: ((float(row.level["lower"])
                                  + float(row.level["upper"])) / 2,
                                 str(row.level["unified_level_id"]))):
            if purchase_ordinal > 3:
                break
            if resistance.level["unified_level_id"] in previous.accepted_ids:
                continue
            add_financial = replace(
                financial, current_purchase_groups=purchase_ordinal - 1)
            proposal = propose_strategy_one_add(
                add_financial, confirmed, resistance, resolutions,
                boundary_ms=boundary_ms, purchase_ordinal=purchase_ordinal,
                fresh_bid=evidence.bid, fresh_ask=evidence.ask,
                prior_accepted_ids=previous.accepted_ids)
            if proposal is None:
                continue
            results = await self.runtime.submit_strategy_one_add(
                replace(proposal, strategy_number=self.contract.strategy_number))
            if (len(results) != 1
                    or results[0].get("order_group") is None
                    or results[0].get("decision", {}).get("status")
                    not in {"approved", "resized"}):
                continue
            purchase_ordinal += 1
