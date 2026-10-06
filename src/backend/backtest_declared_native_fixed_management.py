"""Declared prepared manager; own identity, shared reducers, no installed actor.

Runtime callbacks below are an explicit packet3 boundary, not legacy-number
fallbacks. Captures/references are typed preparation evidence; durable native
checkpoint admission must independently attest them before production use.

100ms/5s/10s fences below are inherited certified producer resolutions,
not new economic thresholds; supported declaration payloads are checked exactly.
"""
from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from collections.abc import Mapping
from uuid import UUID
import re

from .backtest_declared_native_fixed_entry import DeclaredEntryPreparation, DeclaredNativeFixedEntryProposal
from .backtest_declared_base_entry_gate import DeclaredBaseEntryPolicy, _minute
from .backtest_market_data import market_day_boundary
from .backtest_strategy_one_management import StrategyOneClosedPosition
from src.trading_runtime.declared_native_fixed_capabilities import DeclaredNativeFixedCapabilities, _json
from src.trading_runtime.strategy_one_management_evidence import StrategyOneManagementEvidence
from src.trading_runtime.strategy_one_position import ProtectionState, ResistanceBreak, advance_protection, confirm_protection_transition
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_zero_regime_risk_failure import zero_regime_risk_failure, zero_regime_risk_policy_payload
from src.trading_runtime.strategy_profit_giveback import profit_giveback, ProfitGivebackInput
from src.trading_runtime.strategy_profit_giveback_arm import ProfitArmCandidate
from src.trading_runtime.strategy_thirty_three_release import PROFIT_PROTECTION_POLICY
from src.trading_runtime.strategy_thirty_four_release import CONFIRMED_AH_FAILURE_POLICY
from src.trading_runtime.strategy_confirmed_ah_risk_failure import ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure
from src.trading_runtime.strategy_liquidity_fade_failure import LiquidityFadeInput, liquidity_fade_failure, liquidity_fade_policy_payload
from src.trading_runtime.strategy_half_risk_liquidity_fade import half_risk_liquidity_fade_failure, half_risk_liquidity_policy_payload
from src.trading_runtime.early_original_risk_failure import EarlyOriginalRiskPolicy, early_original_risk_failure
from src.trading_runtime.entry_spread_risk import canonical_price_int, exact_epoch_us
from src.trading_runtime.declared_native_management_command import (DeclaredManagementContext, DeclaredExitInputs,
    DeclaredExitCommand, DeclaredProtectionInputs, DeclaredProtectionCommand, DeclaredSessionCommand)


def _clock(value, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= 57_600_000 or value % 100:
        raise ValueError("Declared manager needs an exact completed100ms clock")


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
        raise ValueError("Declared manager needs canonical nonzero identity")


def _projection(value):
    if is_dataclass(value):
        return {field.name: _projection(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {key: _projection(part) for key, part in value.items()}
    if isinstance(value, (tuple, list)):
        return [_projection(part) for part in value]
    if isinstance(value, frozenset):
        return sorted(value)
    return value


@dataclass(frozen=True, slots=True)
class DeclaredManagementPolicy:
    capabilities: DeclaredNativeFixedCapabilities

    def __post_init__(self):
        if type(self.capabilities) is not DeclaredNativeFixedCapabilities:
            raise ValueError("Declared management needs exact typed capabilities")
        self.capabilities.__post_init__()
        DeclaredBaseEntryPolicy(self.capabilities)
        inherited = self.capabilities.payload()["inherited"]
        expected = {"premarket_failure_policy": zero_regime_risk_policy_payload(),
                    "profit_protection_policy": PROFIT_PROTECTION_POLICY,
                    "confirmed_ah_failure_policy": CONFIRMED_AH_FAILURE_POLICY,
                    "liquidity_fade_policy": liquidity_fade_policy_payload(),
                    "half_risk_liquidity_policy": half_risk_liquidity_policy_payload()}
        for name, payload in expected.items():
            if _json(inherited["policies"].get(name)) != _json(payload):
                raise ValueError("Unsupported declared management policy: " + name)
            if payload["policy_id"] not in inherited["rule_set_contracts"]:
                raise ValueError("Declared management policy lacks paired rule")
        flags = inherited["flags"]
        if flags != dict(allows_session_exit=True, allows_adds=False,
                allows_completed_30s_trailing=False, allows_target_escalation=False,
                caps_entry_at_reference_ask=True, allows_followthrough_failure_exit=True):
            raise ValueError("Unsupported declared protection capabilities")
        if inherited["optional_policies"]["armed_profit_floor_policy"] is not None:
            raise ValueError("Declared manager does not select an armed floor")
        early = self.early_policy()
        if (_json(inherited["policies"].get("early_original_risk_failure_policy")) != _json(_projection(early.payload()))
                or early.policy_id not in inherited["rule_set_contracts"]):
            raise ValueError("Declared early-risk payload or rule differs")

    def early_policy(self):
        value = self.capabilities.payload()["inherited"]["optional_policies"]["early_original_risk_policy"]
        if type(value) is not dict:
            raise ValueError("Declared early-risk policy is missing")
        fraction = lambda name: None if value[name] is None else tuple(value[name])
        bounds = value.get("signal_reference_fraction_bounds")
        result = EarlyOriginalRiskPolicy(value["policy_id"], fraction("premarket_fraction"),
            fraction("afterhours_fraction"), value["eligibility_ms"], value.get("require_negative_regime", False),
            None if bounds is None else tuple(tuple(part) for part in bounds))
        if _json(_projection(result.payload())) != _json(value):
            raise ValueError("Declared early-risk schema differs")
        return result

    def liquidation_due(self, boundary_ms):
        session = self.capabilities.payload()["inherited"]["policies"]["session_policy"]
        activation = self.capabilities.payload()["inherited"]["policies"]["activation_policy"]
        origin = _minute(re.fullmatch(r"milliseconds_since_(\d\d:\d\d)_.+", activation["clock"])[1])
        return any((_minute(window["liquidation_start"]) - origin) * 60_000 <= boundary_ms
                   <= (_minute(window["end"]) - origin) * 60_000 for window in session["windows"])


def declared_management_exit(completed, *, policy, prior_arm=None, ten=None, candles=()):
    """Pure inherited priority; deferred liquidity precedes declared extension."""
    if type(policy) is not DeclaredManagementPolicy or type(completed) is not FollowThroughFailureInput:
        raise ValueError("Exit composition needs exact declared policy")
    if prior_arm is not None and type(prior_arm) is not ProfitArmCandidate:
        raise ValueError("Declared exit needs exact prior arm")
    if completed.boundary_ms % 5000 == 0:
        witness = zero_regime_risk_failure(completed)
        if witness is not None:
            return "zero_regime", witness
        if prior_arm is not None and prior_arm.boundary_ms < completed.boundary_ms:
            witness = profit_giveback(ProfitGivebackInput(completed, prior_arm.high_int, prior_arm.boundary_ms))
            if witness is not None:
                return "profit_giveback", witness
        ten = ten or {}
        witness = confirmed_ah_risk_failure(ConfirmedAhRiskFailureInput(completed,
            ten.get("boundary_ms"), ten.get("price_valid") == 1, ten.get("macd_line"), ten.get("macd_signal")))
        if witness is not None:
            return "confirmed_ah", witness
    liquidity = LiquidityFadeInput(completed, candles)
    for name, reducer in (("liquidity_fade", liquidity_fade_failure), ("half_risk_liquidity", half_risk_liquidity_fade_failure)):
        witness = reducer(liquidity)
        if witness is not None:
            return name, witness
    if completed.boundary_ms % 5000 == 0:
        witness = early_original_risk_failure(completed, policy=policy.early_policy())
        if witness is not None:
            return "early_original_risk", witness
    return None


@dataclass(frozen=True, slots=True)
class DeclaredManagementState:
    run_id: str
    source_token: str
    boundary_ms: int
    submitted: tuple
    positions: tuple
    pending_breaks: tuple
    position_highs: tuple
    closed_positions: tuple
    first_held_boundaries: tuple

    def content_hash(self):
        return sha256(_json(_projection(self)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DeclaredProfitArmReference:
    run_id: str
    source_token: str
    candidate: ProfitArmCandidate
    snapshot_id: str
    checkpoint_sequence: int
    journal_batch_id: str
    snapshot_hash: str

    def __post_init__(self):
        for value in (self.run_id, self.snapshot_id, self.journal_batch_id):
            _uuid(value)
        if (type(self.candidate) is not ProfitArmCandidate or type(self.checkpoint_sequence) is not int
                or self.checkpoint_sequence <= 0 or any(type(value) is not str or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value) for value in (self.source_token, self.snapshot_hash))):
            raise ValueError("Declared arm lacks exact checkpoint identity")


@dataclass(frozen=True, slots=True)
class DeclaredLiquidityCheckpointRequest:
    run_id: str
    source_token: str
    kind: str
    completed: LiquidityFadeInput
    witness: object
    financial: StrategyOneFinancialView
    source_entry_intent_id: str
    observation_source: Mapping
    command: DeclaredExitCommand

    def __post_init__(self):
        _uuid(self.run_id)
        _uuid(self.source_entry_intent_id)
        if (type(self.command) is not DeclaredExitCommand or self.command.replay() != (self.kind,self.witness)
                or self.command.context.source.intent_id != self.source_entry_intent_id
                or self.command.context.financial != self.financial
                or self.command.context.run_id != self.run_id or self.command.context.source_token != self.source_token
                or self.command.inputs.completed != self.completed.five_second
                or self.command.inputs.candles != self.completed.candles
                or self.command.context.observation_source != self.observation_source):
            raise ValueError('Declared liquidity request lacks complete priority-replayable command')
        reducer = {"liquidity_fade": liquidity_fade_failure, "half_risk_liquidity": half_risk_liquidity_fade_failure}.get(self.kind)
        if (reducer is None or reducer(self.completed) != self.witness
                or type(self.financial) is not StrategyOneFinancialView
                or self.financial.position_quantity != self.completed.five_second.position_quantity
                or self.financial.pending_exit != self.completed.five_second.pending_exit):
            raise ValueError("Declared liquidity request differs from completed decision")
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
        validate_liquidity_observation_source(self.observation_source)
        object.__setattr__(self, "observation_source", MappingProxyType(dict(self.observation_source)))


class DeclaredNativeFixedManagementRunner:
    """Sequential own state, pure decisions, explicit not-yet-installed sinks."""
    def __init__(self, *, runtime, evidence, preparation, tick_for_ticker, max_pending_breaks=256):
        if (type(preparation) is not DeclaredEntryPreparation or not callable(tick_for_ticker)
                or not callable(getattr(evidence, "management_evidence", None))
                or any(not callable(getattr(runtime, name, None)) for name in
                    ("submit_declared_entry_proposal", "submit_declared_management"))
                or type(max_pending_breaks) is not int or not 1 <= max_pending_breaks <= 65536):
            raise ValueError("Declared manager lacks bounded own-source/runtime interfaces")
        self.runtime, self.evidence, self.preparation = runtime, evidence, preparation
        self.tick_for_ticker, self.max_pending_breaks = tick_for_ticker, max_pending_breaks
        self.policy = DeclaredManagementPolicy(preparation.source.parent.capabilities)
        self._tickers = frozenset(key[0] for key in preparation.source.parent.momentum.keys)
        self._submitted, self._positions, self._pending_breaks = {}, {}, {}
        self._position_highs, self._closed_positions, self._first_held_boundaries = {}, {}, {}
        self._profit_arm_references, self._profit_arm_financials = {}, {}
        self._liquidity_requests, self._latest_five = {}, {}
        self._liquidity_lookup = self._liquidity_sources = None

    def bind_liquidity_source(self, lookup, sources):
        """Bind prepared source once; packet3 must attest complete source identity."""
        if self._liquidity_lookup is not None or not callable(getattr(lookup, "window_at", None)):
            raise ValueError("Declared liquidity lookup is missing or already bound")
        from src.trading_runtime.arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
        copied = {}
        market = self.preparation.source.parent.market
        for ticker in self._tickers:
            source = sources.get(ticker)
            validate_liquidity_observation_source(source)
            if source["source_build_id"] != market.build_id or source["source_market_plan_token"] != market.token:
                raise ValueError("Declared liquidity source differs from market")
            for stage, field in (("bars", "source_bars_attempt_id"), ("technical", "source_indicators_attempt_id"), ("broker_100ms", "source_liquidity_attempt_id")):
                units = [u for u in market.units if u.stage == stage and u.ticker == ticker and u.session_date == market.sessions[0]]
                if len(units) != 1 or units[0].attempt_id != source[field]:
                    raise ValueError("Declared liquidity source differs from pinned producer")
            copied[ticker] = MappingProxyType(dict(source))
        self._liquidity_lookup, self._liquidity_sources = lookup, MappingProxyType(copied)

    def _validate_state(self, state):
        if (type(state) is not DeclaredManagementState or state.run_id != self.preparation.run_id
                or state.source_token != self.preparation.source.token):
            raise ValueError("Declared manager state has foreign run/source")
        _clock(state.boundary_ms, zero=True)
        names = ("submitted", "positions", "pending_breaks", "position_highs", "closed_positions", "first_held_boundaries")
        maps = {}
        for name in names:
            rows = getattr(state, name)
            if type(rows) is not tuple or any(type(row) is not tuple or len(row) != 2 for row in rows):
                raise ValueError("Declared capture requires exact sorted families")
            keys = [key for key, _ in rows]
            if (keys != sorted(set(keys)) or any(type(key) is not tuple or len(key) != 3
                    or any(type(v) is not str or not v for v in key) for key in keys)):
                raise ValueError("Declared capture repeats or changes identity")
            maps[name] = dict(rows)
            if any(key[:2] != (self.preparation.account_id, self.preparation.assignment_id)
                   or key[2] not in self._tickers for key in keys):
                raise ValueError("Declared capture crossed account/assignment/ticker scope")
        active = set(maps["positions"])
        if (not active <= set(maps["submitted"]) or set(maps["position_highs"]) != active
                or set(maps["first_held_boundaries"]) != active or not set(maps["pending_breaks"]) <= active):
            raise ValueError("Declared capture lacks original position sources")
        for key, proposal in maps["submitted"].items():
            if (type(proposal) is not DeclaredNativeFixedEntryProposal or proposal.run_id != state.run_id
                    or proposal.source_token != state.source_token or (proposal.account_id, proposal.assignment_id, proposal.ticker) != key
                    or proposal.strategy_number != self.policy.capabilities.identity.strategy_number
                    or proposal.strategy_id != self.policy.capabilities.identity.strategy_id
                    or proposal.revision != self.policy.capabilities.identity.revision or proposal.boundary_ms > state.boundary_ms):
                raise ValueError("Declared capture changed own entry identity")
        for key, position in maps["positions"].items():
            first = maps["first_held_boundaries"][key]
            _clock(first)
            if (type(position) is not ProtectionState or not maps["submitted"][key].boundary_ms < first <= position.boundary_ms <= state.boundary_ms
                    or any(type(value) not in (int, float) or not isfinite(value) for value in (position.stop, position.target))
                    or not 0 < position.stop < position.target or type(maps["position_highs"][key]) is not int
                    or maps["position_highs"][key] <= 0):
                raise ValueError("Declared capture changed first-held or protection authority")
            _clock(position.boundary_ms)
        for _, pending in maps["pending_breaks"].items():
            if (type(pending) is not tuple or len(pending) > self.max_pending_breaks
                    or any(type(row) is not ResistanceBreak or row.completed_boundary_ms > state.boundary_ms for row in pending)):
                raise ValueError("Declared pending resistance is ahead of capture")
        for _, closed in maps["closed_positions"].items():
            if (type(closed) is not StrategyOneClosedPosition or not 0 < closed.closed_boundary_ms <= state.boundary_ms
                    or type(closed.high_int) is not int or closed.high_int <= 0):
                raise ValueError("Declared closed position is invalid")
        return maps

    def capture_state(self, *, boundary_ms):
        state = DeclaredManagementState(self.preparation.run_id, self.preparation.source.token, boundary_ms,
            tuple(sorted(self._submitted.items())), tuple(sorted(self._positions.items())),
            tuple(sorted((key, tuple(ResistanceBreak(row.completed_boundary_ms, MappingProxyType(dict(row.level))) for row in value)) for key, value in self._pending_breaks.items())),
            tuple(sorted(self._position_highs.items())), tuple(sorted(self._closed_positions.items())), tuple(sorted(self._first_held_boundaries.items())))
        self._validate_state(state)
        return state

    def restore_state(self, state, *, source_authority):
        """Native restore stays closed until independently attested own family."""
        from src.trading_runtime.arte_declared_native_fixed_sources import PreparedDeclaredSourceResolver
        if type(source_authority) is not PreparedDeclaredSourceResolver:
            raise ValueError("Declared native restore requires exact independent source resolver")
        self._validate_state(state)
        source_authority.verify_manager_state(state)
        raise RuntimeError("Declared native restore assembly is not installed")

    def restore_prepared_state(self, state, *, predecessor_financials):
        """Prepared recovery; native attested snapshot admission is packet3-owned.

        Caller-provided historical financial fixtures re-prove each proposal
        against independently prepared sources; this is not cold authority.
        Reentry captures require the future historical reentry witness hook.
        Profit/checkpoint references are ephemeral and must be reconfirmed.
        """
        if self._submitted or self._positions or self._closed_positions:
            raise RuntimeError("Declared manager is already active")
        maps = self._validate_state(state)
        financials = dict(predecessor_financials)
        if len(financials) != len(predecessor_financials) or set(financials) != set(maps["submitted"]):
            raise ValueError("Declared restore lacks exact historical entry financials")
        for key, proposal in maps["submitted"].items():
            self.preparation.verify_proposal(proposal, financials[key])
        self._submitted, self._positions = maps["submitted"], maps["positions"]
        self._pending_breaks = {key: list(value) for key, value in maps["pending_breaks"].items()}
        self._position_highs, self._closed_positions = maps["position_highs"], maps["closed_positions"]
        self._first_held_boundaries = maps["first_held_boundaries"]

    async def on_entry_proposal(self, proposal, financial, *, reentry=None):
        self.preparation.verify_proposal(proposal, financial, reentry=reentry)
        key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
        if key in self._submitted:
            raise RuntimeError("Declared assignment already owns an entry")
        results = await self.runtime.submit_declared_entry_proposal(proposal, financial)
        if len(results) == 1 and results[0].get("order_group") is not None and results[0].get("decision", {}).get("status") in {"approved", "resized"}:
            self._submitted[key] = proposal
        return results

    def last_closed_position(self, financial):
        return self._closed_positions.get((financial.account_id, financial.assignment_id, financial.ticker))

    def profit_arming_requests(self, *, boundary_ms):
        state = self.capture_state(boundary_ms=boundary_ms)
        requests = []
        for key, financial in sorted(self._profit_arm_financials.items()):
            if key not in self._positions or key in self._profit_arm_references or financial.position_quantity <= 0 or financial.pending_exit:
                continue
            proposal, high = self._submitted[key], self._position_highs[key]
            if Decimal(high) >= (2 * Decimal(str(proposal.reference_ask)) - Decimal(str(proposal.initial_stop))) * 10000:
                requests.append((ProfitArmCandidate(*key, boundary_ms, self._first_held_boundaries[key], proposal.reference_ask, proposal.initial_stop, high), financial))
        return tuple(requests)

    def accept_profit_arming_references(self, requests, references, *, boundary_ms):
        if type(references) is not tuple or not requests or requests != self.profit_arming_requests(boundary_ms=boundary_ms) or len(requests) != len(references):
            raise ValueError("Declared arming confirmation changed candidates")
        state = self.capture_state(boundary_ms=boundary_ms)
        for (candidate, _), reference in zip(requests, references, strict=True):
            if (type(reference) is not DeclaredProfitArmReference or reference.candidate != candidate
                    or reference.run_id != state.run_id or reference.source_token != state.source_token or reference.snapshot_hash != state.content_hash()):
                raise ValueError("Declared arming confirmation changed checkpoint source")
        for reference in references:
            candidate = reference.candidate
            self._profit_arm_references[(candidate.account_id, candidate.assignment_id, candidate.ticker)] = reference

    def liquidity_fade_requests(self, *, boundary_ms):
        requests = tuple(value for _, value in sorted(self._liquidity_requests.items()))
        if any(value.witness.boundary_ms != boundary_ms for value in requests):
            raise RuntimeError("Declared liquidity checkpoint must finish before advancing")
        return requests

    def complete_liquidity_fade_requests(self, requests, *, boundary_ms):
        if requests != self.liquidity_fade_requests(boundary_ms=boundary_ms):
            raise ValueError("Declared liquidity retry changed completed candidates")
        self._liquidity_requests.clear()

    async def on_management(self, financial, resolutions, boundary_ms):
        _clock(boundary_ms)
        if (type(financial) is not StrategyOneFinancialView or financial.assignment_id != self.preparation.assignment_id
                or financial.account_id != self.preparation.account_id
                or financial.ticker not in self._tickers
                or type(financial.position_quantity) not in (int, float) or not isfinite(financial.position_quantity)
                or financial.position_quantity < 0 or type(financial.pending_entry) is not bool or type(financial.pending_exit) is not bool):
            raise ValueError("Declared management requires typed financial view")
        if self._liquidity_requests:
            self.liquidity_fade_requests(boundary_ms=boundary_ms)
        key = (financial.account_id, financial.assignment_id, financial.ticker)
        self._profit_arm_financials[key] = financial
        if financial.position_quantity <= 0:
            if not financial.pending_entry and not financial.pending_exit:
                source, high = self._submitted.get(key), self._position_highs.get(key)
                if source is not None and high is not None:
                    self._closed_positions[key] = StrategyOneClosedPosition(boundary_ms, source.bos_support_level_id, high)
                for family in (self._submitted, self._positions, self._position_highs, self._first_held_boundaries, self._pending_breaks, self._profit_arm_references, self._profit_arm_financials):
                    family.pop(key, None)
            return
        source = self._submitted.get(key)
        if source is None:
            raise RuntimeError("Declared held position lacks its own original entry")
        def context():
            return DeclaredManagementContext(self.preparation.run_id,self.preparation.source.token,self.preparation,
                date.fromisoformat(self.preparation.source.parent.market.sessions[0]),source,financial,self.policy,
                boundary_ms,self._first_held_boundaries.get(key),self._liquidity_sources[financial.ticker])
        if self.policy.liquidation_due(boundary_ms):
            command=DeclaredSessionCommand(context(),resolutions)
            command.replay()
            await self.runtime.submit_declared_management(command)
            return
        if key not in self._positions:
            if boundary_ms <= source.boundary_ms:
                raise ValueError("Declared first held bucket must follow entry proposal")
            self._positions[key] = ProtectionState(boundary_ms, source.initial_stop, source.initial_target)
            self._first_held_boundaries[key] = boundary_ms
            self._position_highs[key] = canonical_price_int(source.reference_ask)
            return
        previous = self._positions[key]
        if boundary_ms <= previous.boundary_ms:
            raise ValueError("Declared management clock did not advance")
        bar100 = resolutions.get(100) or {}
        if bar100.get("price_valid") == 1:
            high = bar100.get("high_int")
            if type(high) is not int or high <= 0:
                raise ValueError("Declared held bucket lacks native high")
            self._position_highs[key] = max(self._position_highs[key], high)
        evidence = await self.evidence.management_evidence(financial.ticker, resolutions, boundary_ms=boundary_ms)
        if type(evidence) is not StrategyOneManagementEvidence or evidence.ticker != financial.ticker or evidence.boundary_ms != boundary_ms:
            raise ValueError("Declared management evidence changed boundary")
        if self._liquidity_lookup is None:
            raise RuntimeError("Declared management lacks prepared liquidity source")
        five = resolutions.get(5000)
        if five is not None:
            at = five.get("boundary_ms")
            if type(at) is not int or at % 5000 or not 0 < at <= boundary_ms:
                raise ValueError("Declared liquidity requires completed5s source")
            self._latest_five[financial.ticker] = MappingProxyType(dict(five))
        five = self._latest_five.get(financial.ticker) or {}
        now = exact_epoch_us(market_day_boundary(date.fromisoformat(self.preparation.source.parent.market.sessions[0]), boundary_ms))
        quote_us = bar100.get("quote_timestamp_us")
        age = now - quote_us if type(quote_us) is int and bar100.get("quote_valid") == 1 else None
        completed = FollowThroughFailureInput(boundary_ms, self._first_held_boundaries[key], source.reference_ask, source.initial_stop,
            five.get("boundary_ms"), five.get("close_int"), five.get("price_valid") == 1, five.get("macd_line"), five.get("macd_signal"),
            evidence.bid, evidence.ask, age, financial.position_quantity, financial.pending_exit)
        window = self._liquidity_lookup.window_at(financial.ticker, boundary_ms)
        candles = () if window is None else window.candles
        reference = self._profit_arm_references.get(key)
        selected = declared_management_exit(completed, policy=self.policy, prior_arm=None if reference is None else reference.candidate,
            ten=resolutions.get(10000), candles=candles)
        exit_inputs=DeclaredExitInputs(completed,resolutions.get(10000) or {},candles,reference)
        if selected is not None:
            kind, witness = selected
            command=DeclaredExitCommand(context(),exit_inputs,kind,witness)
            command.replay()
            if kind in {"liquidity_fade", "half_risk_liquidity"}:
                request = DeclaredLiquidityCheckpointRequest(self.preparation.run_id, self.preparation.source.token, kind,
                    LiquidityFadeInput(completed, candles), witness, financial, source.intent_id, self._liquidity_sources[financial.ticker],command)
                if key in self._liquidity_requests and self._liquidity_requests[key] != request:
                    raise RuntimeError("Declared liquidity retry changed decision")
                if key not in self._liquidity_requests and len(self._liquidity_requests) >= 65_536:
                    raise RuntimeError("Declared pending liquidity exceeds inherited memory bound")
                self._liquidity_requests[key] = request
            else:
                await self.runtime.submit_declared_management(command)
            self._profit_arm_financials.pop(key, None)
            return
        pending = self._pending_breaks.setdefault(key, [])
        seen = {(row.completed_boundary_ms, row.level.get("unified_level_id")) for row in pending}
        additions = [row for row in evidence.breaks if (row.completed_boundary_ms, row.level.get("unified_level_id")) not in seen]
        if len(pending) + len(additions) > self.max_pending_breaks:
            raise RuntimeError("Declared pending resistance exceeds memory bound")
        pending.extend(ResistanceBreak(row.completed_boundary_ms, MappingProxyType(dict(row.level))) for row in additions)
        if evidence.bid is None or evidence.ask is None or financial.pending_exit:
            return
        tick = self.tick_for_ticker(financial.ticker)
        protection_inputs=DeclaredProtectionInputs(previous,boundary_ms,evidence.bid,evidence.ask,tick,
            evidence.low_boundary_ms,evidence.low_int,evidence.low_int is not None,evidence.low_int is not None,
            tuple(pending),tuple(evidence.overhead_levels),evidence.price_bearing_bar)
        transition=protection_inputs.replay(context(),exit_inputs)
        command=DeclaredProtectionCommand(context(),exit_inputs,protection_inputs,transition)
        command.replay()
        if transition.stop_amendment is None and transition.target_amendment is None:
            confirmed = confirm_protection_transition(previous, transition, target_confirmed=False, stop_confirmed=False)
        else:
            confirmed = await self.runtime.submit_declared_management(command)
        if (type(confirmed) is not ProtectionState or confirmed.boundary_ms != boundary_ms
                or not previous.accepted_ids <= confirmed.accepted_ids or not 0 < confirmed.stop < confirmed.target):
            raise RuntimeError("Declared OMS did not confirm consistent protection")
        self._positions[key] = confirmed
        pending.clear()
