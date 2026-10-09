"""Standalone declared causal exit reducer; no lookup or order authority.

Source identities are checked for consistency, not certified here. A native
owner must independently certify observations, restore state and apply the
inherited exit priority before using a returned witness.
"""
from dataclasses import dataclass, replace
from datetime import date
from math import isfinite
from uuid import UUID
import re


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(name + ' must be an exact bounded integer')


def _hash(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Exact source content identity required')


@dataclass(frozen=True, slots=True)
class StructuralRejectionPolicy:
    policy_id: str = 'profit-armed-structural-rejection-liquidity@1'
    decision_resolution_ms: int = 5000
    bar_resolution_ms: int = 5000
    arm_original_risk: tuple[int, int] = (1, 1)
    rejection_count: int = 2
    activity_count: int = 4
    recent_activity_multiplier: int = 2
    maximum_quote_age_us: int = 1000000

    def __post_init__(self):
        if (self.policy_id != 'profit-armed-structural-rejection-liquidity@1'
                or type(self.policy_id) is not str):
            raise ValueError('Exact declared structural rejection policy required')
        for name in ('decision_resolution_ms', 'bar_resolution_ms', 'rejection_count',
                     'activity_count', 'recent_activity_multiplier', 'maximum_quote_age_us'):
            _integer(getattr(self, name), name, 1)
        if (not 100 <= self.bar_resolution_ms <= 60000 or self.bar_resolution_ms % 100
                or self.decision_resolution_ms % 100
                or self.bar_resolution_ms % self.decision_resolution_ms
                or not 1 <= self.rejection_count <= 16
                or not 2 <= self.activity_count <= 32 or self.activity_count % 2
                or not 1 <= self.recent_activity_multiplier <= 64
                or not 1 <= self.maximum_quote_age_us <= 5000000
                or type(self.arm_original_risk) is not tuple
                or len(self.arm_original_risk) != 2
                or any(type(v) is not int or not 1 <= v <= 1000 for v in self.arm_original_risk)
                or self.arm_original_risk[0] > 4 * self.arm_original_risk[1]):
            raise ValueError('Unsupported declared structural rejection parameters')

    def payload(self):
        return dict(policy_id=self.policy_id,
            decision_resolution_ms=self.decision_resolution_ms,
            bar_resolution_ms=self.bar_resolution_ms,
            arm_original_risk=self.arm_original_risk,
            rejection_count=self.rejection_count, activity_count=self.activity_count,
            recent_activity_multiplier=self.recent_activity_multiplier,
            maximum_quote_age_us=self.maximum_quote_age_us,
            arm='strictly_prior_wholly_held_completed_high',
            cross='completed_close_strictly_above_prior_known_frozen_resistance',
            rejection='contiguous_wholly_held_closes_strictly_below_same_resistance',
            bid='fresh_current_bid_strictly_below_resistance',
            momentum='latest_completed_bar_macd_line_strictly_below_signal',
            liquidity='positive_prior_half_and_multiplier_recent_half_at_most_prior_half',
            missing='retain_valid_predecessor_no_added_exit',
            precedence='inherited_exits_first', price_unit='canonical_1/10000',
            repeated_completed_bar='no_streak_advance; fresh_current_quote_may_confirm_once')


@dataclass(frozen=True, slots=True)
class RejectionSource:
    run_id: str
    ticker: str
    session_date: str
    market_build_id: str
    market_plan_token: str
    bars_attempt_id: str
    indicators_attempt_id: str
    liquidity_attempt_id: str
    interval_plan_token: str

    def __post_init__(self):
        if (type(self.ticker) is not str or not self.ticker
                or type(self.session_date) is not str
                or date.fromisoformat(self.session_date).isoformat() != self.session_date):
            raise ValueError('Exact source session/ticker required')
        for name in ('run_id', 'bars_attempt_id', 'indicators_attempt_id', 'liquidity_attempt_id'):
            v = getattr(self, name)
            if type(v) is not str or str(UUID(v)) != v:
                raise ValueError('Canonical source attempt/run UUID required')
        if type(self.market_build_id) is not str or not self.market_build_id.strip():
            raise ValueError('Exact producer-owned build String required')
        for name in ('market_plan_token', 'interval_plan_token'):
            _hash(getattr(self, name))


@dataclass(frozen=True, slots=True)
class HeldBar:
    source: RejectionSource
    boundary_ms: int
    close_int: int
    high_int: int
    trade_count: int
    price_valid: bool = True
    macd_line: float | None = None
    macd_signal: float | None = None
    resolution_ms: int = 5000
    extremes_valid: bool = True

    def __post_init__(self):
        _source(self.source)
        _integer(self.boundary_ms, 'bar boundary', 1)
        _integer(self.close_int, 'completed close')
        _integer(self.high_int, 'completed high')
        _integer(self.trade_count, 'trade count')
        _integer(self.resolution_ms, 'bar resolution', 100)
        if (self.resolution_ms > 60000 or self.resolution_ms % 100
                or self.boundary_ms % self.resolution_ms or self.trade_count > (1 << 64) - 1
                or type(self.price_valid) is not bool or type(self.extremes_valid) is not bool
                or self.price_valid and (self.close_int <= 0
                    or self.extremes_valid and self.close_int > self.high_int)):
            raise ValueError('Malformed completed producer bar')
        for v in (self.macd_line, self.macd_signal):
            if v is not None and type(v) not in (int, float):
                raise ValueError('Malformed completed momentum')


@dataclass(frozen=True, slots=True)
class FrozenResistance:
    source: RejectionSource
    level_id: str
    geometry_hash: str
    lower_int: int
    upper_int: int
    price_int: int
    confirmed_boundary_ms: int
    available_boundary_ms: int
    role: str = 'resistance'

    def __post_init__(self):
        _source(self.source); _hash(self.geometry_hash)
        for name in ('lower_int', 'upper_int', 'price_int'):
            _integer(getattr(self, name), name, 1)
        for name in ('confirmed_boundary_ms', 'available_boundary_ms'):
            _integer(getattr(self, name), name)
        if (type(self.level_id) is not str or not self.level_id or self.role != 'resistance'
                or not self.lower_int <= self.price_int <= self.upper_int
                or self.confirmed_boundary_ms > self.available_boundary_ms):
            raise ValueError('Malformed frozen resistance geometry')


@dataclass(frozen=True, slots=True)
class CurrentQuote:
    source: RejectionSource
    observed_at_us: int
    bid_int: int
    ask_int: int

    def __post_init__(self):
        _source(self.source)
        _integer(self.observed_at_us, 'quote clock', 1)
        _integer(self.bid_int, 'bid', 1); _integer(self.ask_int, 'ask', 1)


def _source(source):
    if type(source) is not RejectionSource:
        raise ValueError('Exact typed source identity required')
    source.__post_init__()


@dataclass(frozen=True, slots=True)
class StructuralRejectionState:
    source: RejectionSource
    position_intent_id: str
    account_id: str
    session_origin_us: int
    first_held_boundary_ms: int
    original_ask_int: int
    original_stop_int: int
    last_decision_boundary_ms: int = 0
    last_bar: HeldBar | None = None
    arm: HeldBar | None = None
    resistance: FrozenResistance | None = None
    cross: HeldBar | None = None
    cross_prior: HeldBar | None = None
    rejections: tuple[HeldBar, ...] = ()
    fired: bool = False
    policy: StructuralRejectionPolicy = StructuralRejectionPolicy()

    def __post_init__(self):
        _source(self.source)
        if type(self.policy) is not StructuralRejectionPolicy:
            raise ValueError('Exact state declaration required')
        self.policy.__post_init__()
        if (type(self.position_intent_id) is not str
                or str(UUID(self.position_intent_id)) != self.position_intent_id
                or type(self.account_id) is not str or not self.account_id):
            raise ValueError('Exact held position identity required')
        for name in ('session_origin_us', 'original_ask_int', 'original_stop_int'):
            _integer(getattr(self, name), name, 1)
        _integer(self.first_held_boundary_ms, 'first held boundary')
        _integer(self.last_decision_boundary_ms, 'last decision')
        if self.original_stop_int >= self.original_ask_int or type(self.fired) is not bool:
            raise ValueError('Malformed original risk/state')
        if type(self.rejections) is not tuple or len(self.rejections) > self.policy.rejection_count:
            raise ValueError('Bounded persisted rejection chain required')
        for bar in (self.last_bar, self.arm, self.cross, self.cross_prior, *self.rejections):
            if bar is not None:
                if type(bar) is not HeldBar:
                    raise ValueError('Exact persisted completed bar required')
                bar.__post_init__()
                if (bar.source != self.source or bar.boundary_ms > self.last_decision_boundary_ms
                        or bar.resolution_ms != self.policy.bar_resolution_ms
                        or bar.boundary_ms - self.policy.bar_resolution_ms < self.first_held_boundary_ms):
                    raise ValueError('Persisted bar has foreign/future/unheld provenance')
        bars = tuple(b for b in (self.last_bar, self.arm, self.cross, self.cross_prior,
                                  *self.rejections) if b is not None)
        if any(a.boundary_ms == b.boundary_ms and a != b
               for i, a in enumerate(bars) for b in bars[i + 1:]):
            raise ValueError('Persisted observations contradict identical source clocks')
        if bars and (self.last_bar is None
                     or any(b.boundary_ms > self.last_bar.boundary_ms for b in bars)):
            raise ValueError('Persisted observation frontier contradicts predecessor state')
        if self.fired and (len(self.rejections) != self.policy.rejection_count
                           or self.last_bar != self.rejections[-1]):
            raise ValueError('Fired state lacks complete causal rejection chain')
        if ((self.resistance is None) != (self.cross is None)
                or (self.cross_prior is None) != (self.cross is None)):
            raise ValueError('Frozen geometry and cross must be paired')
        if self.arm is not None and (not self.arm.price_valid or not self.arm.extremes_valid
                or self.arm.high_int * self.policy.arm_original_risk[1]
                < self.original_ask_int * self.policy.arm_original_risk[1]
                + (self.original_ask_int - self.original_stop_int) * self.policy.arm_original_risk[0]):
            raise ValueError('Persisted arm does not reach original1R')
        if self.cross is not None:
            if type(self.resistance) is not FrozenResistance:
                raise ValueError('Exact persisted resistance required')
            self.resistance.__post_init__()
            if (self.resistance.source != self.source or self.arm is None
                    or not self.arm.boundary_ms < self.cross.boundary_ms
                    or not self.resistance.available_boundary_ms < self.cross.boundary_ms
                    or not self.cross.price_valid
                    or self.cross.close_int <= self.resistance.price_int
                    or not self.cross_prior.price_valid
                    or self.cross_prior.close_int > self.resistance.price_int
                    or self.cross.boundary_ms - self.cross_prior.boundary_ms != self.policy.bar_resolution_ms):
                raise ValueError('Persisted cross lacks strictly prior arm/geometry')
        for rejection in self.rejections:
            if (self.cross is None or not self.cross.boundary_ms < rejection.boundary_ms
                    or not rejection.price_valid or rejection.close_int >= self.resistance.price_int):
                raise ValueError('Persisted rejection lacks frozen crossed resistance')
        if any(b.boundary_ms - a.boundary_ms != self.policy.bar_resolution_ms
               for a, b in zip(self.rejections, self.rejections[1:])):
            raise ValueError('Persisted rejection chain is not contiguous')



@dataclass(frozen=True, slots=True)
class StructuralRejectionInput:
    source: RejectionSource
    boundary_ms: int
    completed_bar: HeldBar | None = None
    resistance: FrozenResistance | None = None
    activity: tuple[HeldBar, ...] = ()
    quote: CurrentQuote | None = None
    higher_priority_exit: bool = False
    pending_exit: bool = False
    position_held: bool = True


@dataclass(frozen=True, slots=True)
class StructuralRejectionWitness:
    policy: StructuralRejectionPolicy
    predecessor: StructuralRejectionState
    decision_boundary_ms: int
    arm: HeldBar
    resistance: FrozenResistance
    cross: HeldBar
    cross_prior: HeldBar
    rejections: tuple[HeldBar, ...]
    activity: tuple[HeldBar, ...]
    quote: CurrentQuote


def reduce_structural_rejection(state, value, *, policy):
    """Return next immutable state and optional fully causal added-exit witness."""
    if (type(state) is not StructuralRejectionState
            or type(value) is not StructuralRejectionInput
            or type(policy) is not StructuralRejectionPolicy):
        raise ValueError('Exact typed reducer state/input/policy required')
    state.__post_init__(); policy.__post_init__(); _source(value.source)
    if policy != state.policy:
        raise ValueError('Reducer declaration differs from predecessor state')
    _integer(value.boundary_ms, 'decision boundary', 1)
    if (value.source != state.source or value.boundary_ms <= state.last_decision_boundary_ms
            or value.boundary_ms % policy.decision_resolution_ms):
        raise ValueError('Foreign, unordered or nondeclared decision clock')
    if any(type(getattr(value, n)) is not bool for n in
           ('higher_priority_exit', 'pending_exit', 'position_held')):
        raise ValueError('Exact caller priority/held guards required')
    if type(value.activity) is not tuple or len(value.activity) > policy.activity_count:
        raise ValueError('Bounded typed completed activity required')
    allbars = (*value.activity, *((value.completed_bar,) if value.completed_bar is not None else ()))
    for bar in allbars:
        if type(bar) is not HeldBar:
            raise ValueError('Exact producer observations required')
        bar.__post_init__()
        if (bar.source != state.source or bar.boundary_ms > value.boundary_ms
                or bar.resolution_ms != policy.bar_resolution_ms):
            raise ValueError('Foreign or future completed source observation')
    if len({b.boundary_ms for b in value.activity}) != len(value.activity):
        raise ValueError('Duplicate activity source clock')
    if any(a.boundary_ms >= b.boundary_ms for a, b in zip(value.activity, value.activity[1:])):
        raise ValueError('Activity clocks are unordered')
    if value.resistance is not None:
        if type(value.resistance) is not FrozenResistance:
            raise ValueError('Exact frozen geometry required')
        value.resistance.__post_init__()
        if (value.resistance.source != state.source
                or value.resistance.available_boundary_ms > value.boundary_ms):
            raise ValueError('Foreign or future resistance source')
        if state.resistance is not None and value.resistance != state.resistance:
            raise ValueError('Bound cross geometry cannot be replaced')
    if value.quote is not None:
        if type(value.quote) is not CurrentQuote:
            raise ValueError('Exact current quote required')
        value.quote.__post_init__()
        if (value.quote.source != state.source or value.quote.observed_at_us
                > state.session_origin_us + value.boundary_ms * 1000):
            raise ValueError('Foreign or future quote')
    next_state = replace(state, last_decision_boundary_ms=value.boundary_ms)
    bar = value.completed_bar
    if (not value.position_held or state.fired or bar is None
            or bar.boundary_ms - policy.bar_resolution_ms < state.first_held_boundary_ms
            or not 0 <= value.boundary_ms - bar.boundary_ms < policy.bar_resolution_ms):
        return next_state, None
    repeated = False
    if state.last_bar is not None:
        if bar.boundary_ms < state.last_bar.boundary_ms:
            raise ValueError('Completed bar moved backwards')
        if bar.boundary_ms == state.last_bar.boundary_ms:
            if bar != state.last_bar:
                raise ValueError('Completed source bar changed at same clock')
            repeated = True
    observed = tuple(b for b in (state.last_bar, state.arm, state.cross, state.cross_prior, *state.rejections)
                     if b is not None)
    for candle in value.activity:
        if any(candle.boundary_ms == b.boundary_ms and candle != b for b in (*observed, bar)):
            raise ValueError('Conflicting completed source content at same clock')
    next_state = replace(next_state, last_bar=bar)
    if not bar.price_valid:
        return replace(next_state, rejections=()), None
    # Arming and crossing cannot share a completed bar. Arm is frozen once earned.
    if state.arm is None:
        if (bar.extremes_valid and bar.high_int * policy.arm_original_risk[1]
                >= state.original_ask_int * policy.arm_original_risk[1]
                + (state.original_ask_int - state.original_stop_int) * policy.arm_original_risk[0]):
            next_state = replace(next_state, arm=bar)
        return next_state, None
    if state.cross is None:
        level = value.resistance
        if (level is not None and state.arm.boundary_ms < bar.boundary_ms
                and level.available_boundary_ms < bar.boundary_ms
                and bar.close_int > level.price_int
                and state.last_bar is not None and state.last_bar.price_valid
                and state.last_bar.close_int <= level.price_int
                and bar.boundary_ms - state.last_bar.boundary_ms == policy.bar_resolution_ms):
            next_state = replace(next_state, resistance=level, cross=bar, cross_prior=state.last_bar)
        return next_state, None
    if bar.close_int >= state.resistance.price_int:
        return replace(next_state, rejections=()), None
    if repeated:
        rejections = state.rejections
    else:
        previous = state.rejections
        if previous and bar.boundary_ms - previous[-1].boundary_ms != policy.bar_resolution_ms:
            previous = ()
        rejections = (*previous, bar)[-policy.rejection_count:]
        next_state = replace(next_state, rejections=rejections)
    if (len(rejections) != policy.rejection_count
            or value.higher_priority_exit or value.pending_exit):
        return next_state, None
    candles = value.activity
    if (len(candles) != policy.activity_count or candles[-1] != bar
            or any(b.boundary_ms - policy.bar_resolution_ms < state.first_held_boundary_ms
                   for b in candles)
            or any(b.boundary_ms - a.boundary_ms != policy.bar_resolution_ms
                   for a, b in zip(candles, candles[1:]))) :
        return next_state, None
    half = policy.activity_count // 2
    prior10 = sum(b.trade_count for b in candles[:half])
    recent10 = sum(b.trade_count for b in candles[half:])
    quote = value.quote
    if (prior10 <= 0 or policy.recent_activity_multiplier * recent10 > prior10
            or bar.macd_line is None or bar.macd_signal is None
            or not isfinite(bar.macd_line) or not isfinite(bar.macd_signal)
            or bar.macd_line >= bar.macd_signal or quote is None
            or not quote.bid_int <= quote.ask_int
            or quote.bid_int >= state.resistance.price_int
            or state.session_origin_us + value.boundary_ms * 1000 - quote.observed_at_us
                > policy.maximum_quote_age_us):
        return next_state, None
    witness = StructuralRejectionWitness(policy, state, value.boundary_ms,
        state.arm, state.resistance, state.cross, state.cross_prior, rejections, candles, quote)
    return replace(next_state, fired=True), witness
