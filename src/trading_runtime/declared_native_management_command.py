"""Producer-replayable prepared commands; not installed/cold source authority.

Pure replay compares the claimed decision to the original reducers. Fresh
producer certification and durable predecessor/OMS verification are separate
required installed gates; these dataclasses do not manufacture that authority.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from math import isfinite
from types import MappingProxyType

from src.backend.backtest_declared_native_fixed_entry import DeclaredNativeFixedEntryProposal, DeclaredEntryPreparation
from .strategy_followthrough_failure import FollowThroughFailureInput
from .strategy_one_stateful import StrategyOneFinancialView
from .strategy_one_position import (ProtectionState, ProtectionTransition, ResistanceBreak,
    advance_protection)
from .strategy_liquidity_fade_failure import LiquidityFadeCandle


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(part) for key, part in value.items()})
    if type(value) in (tuple, list):
        return tuple(_freeze(part) for part in value)
    return value


def _equal(left, right):
    """Compare typed claims without Python's bool/int or int/float aliases."""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(_equal(left[k], right[k]) for k in left)
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_equal(getattr(left, f.name), getattr(right, f.name)) for f in fields(left))
    if type(left) is tuple:
        return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _clock(value, resolution=100):
    if (type(resolution) is not int or resolution < 100 or resolution % 100
            or type(value) is not int or not 0 < value <= 57_600_000 or value % resolution):
        raise ValueError('Declared management command clock is malformed')


@dataclass(frozen=True, slots=True)
class DeclaredManagementContext:
    run_id: str
    source_token: str
    preparation: DeclaredEntryPreparation
    session_date: date
    source: DeclaredNativeFixedEntryProposal
    financial: StrategyOneFinancialView
    policy: object
    boundary_ms: int
    first_held_boundary_ms: int | None
    observation_source: Mapping

    def __post_init__(self):
        from src.backend.backtest_declared_native_fixed_management import DeclaredManagementPolicy, _uuid
        _uuid(self.run_id); _clock(self.boundary_ms)
        if (type(self.preparation) is not DeclaredEntryPreparation or type(self.session_date) is not date
                or type(self.policy) is not DeclaredManagementPolicy
                or type(self.source) is not DeclaredNativeFixedEntryProposal
                or type(self.financial) is not StrategyOneFinancialView):
            raise ValueError('Declared management context needs exact own types')
        self.policy.__post_init__()
        self.preparation.__post_init__()
        source, financial = self.source, self.financial
        if (type(source.strategy_number) is not int or type(source.revision) is not int
                or any(type(x) is not str for x in (source.run_id, source.source_token,
                    source.account_id, source.assignment_id, source.strategy_id, source.ticker,
                    financial.account_id, financial.assignment_id, financial.ticker))):
            raise ValueError('Declared management identity scalar aliases are forbidden')
        prep=self.preparation
        if ((prep.run_id,prep.source.token,prep.account_id,prep.assignment_id)
                != (self.run_id,self.source_token,source.account_id,source.assignment_id)
                or prep.source.parent.capabilities != self.policy.capabilities
                or prep.source.parent.market.sessions != (self.session_date.isoformat(),)):
            raise ValueError('Declared management context changed actual preparation')
        identity = self.policy.capabilities.identity
        if (type(self.source_token) is not str or len(self.source_token) != 64
                or any(c not in '0123456789abcdef' for c in self.source_token)
                or (source.run_id,source.source_token,source.strategy_id,source.strategy_number,source.revision)
                    != (self.run_id,self.source_token,identity.strategy_id,identity.strategy_number,identity.revision)
                or (financial.account_id,financial.assignment_id,financial.ticker)
                    != (source.account_id,source.assignment_id,source.ticker)
                or type(financial.position_quantity) not in (int,float)
                or not isfinite(financial.position_quantity) or financial.position_quantity <= 0
                or any(type(v) is not bool for v in (financial.pending_entry,financial.pending_exit,financial.pending_capital_request))):
            raise ValueError('Declared management context has foreign original source/financial scope')
        _clock(source.boundary_ms)
        if self.first_held_boundary_ms is not None:
            _clock(self.first_held_boundary_ms)
            if not source.boundary_ms < self.first_held_boundary_ms <= self.boundary_ms:
                raise ValueError('Declared management first-held clock differs')
        from .arte_liquidity_fade_failure_v4 import validate_liquidity_observation_source
        validate_liquidity_observation_source(self.observation_source)
        market=prep.source.parent.market
        if (self.observation_source['source_build_id'] != market.build_id
                or self.observation_source['source_market_plan_token'] != market.token):
            raise ValueError('Declared management producer references changed actual preparation')
        for stage,name in (('bars','source_bars_attempt_id'),('technical','source_indicators_attempt_id'),('broker_100ms','source_liquidity_attempt_id')):
            units=tuple(row for row in market.units if row.ticker==source.ticker and row.stage==stage)
            if len(units)!=1 or self.observation_source[name]!=units[0].attempt_id:
                raise ValueError('Declared management producer attempt differs from actual preparation')
        object.__setattr__(self,'observation_source',_freeze(self.observation_source))


@dataclass(frozen=True, slots=True)
class DeclaredExitInputs:
    completed: FollowThroughFailureInput
    ten: Mapping
    candles: tuple[LiquidityFadeCandle, ...]
    prior_arm: object = None

    def __post_init__(self):
        if type(self.completed) is not FollowThroughFailureInput or not isinstance(self.ten,Mapping) or type(self.candles) is not tuple:
            raise ValueError('Declared exit command lacks full producer inputs')
        if any(type(row) is not LiquidityFadeCandle for row in self.candles):
            raise ValueError('Declared exit command has foreign liquidity candles')
        object.__setattr__(self,'ten',_freeze(self.ten))

    def replay(self, context):
        from src.backend.backtest_declared_native_fixed_management import (DeclaredProfitArmReference, declared_management_exit)
        context.__post_init__(); self.__post_init__()
        value = self.completed
        _clock(value.boundary_ms); _clock(value.first_held_boundary_ms)
        if any(type(x) not in (int, float) or not isfinite(x) for x in
                (value.reference_ask, value.initial_stop, value.position_quantity)) or any(
                type(x) is not bool for x in (value.price_valid, value.pending_exit)):
            raise ValueError('Declared exit producer scalars are malformed')
        if value.completed_five_second_boundary_ms is not None:
            _clock(value.completed_five_second_boundary_ms, 5000)
            if value.completed_five_second_boundary_ms > context.boundary_ms:
                raise ValueError('Declared exit5s source is future')
        if value.completed_five_second_close_int is not None and type(value.completed_five_second_close_int) is not int:
            raise ValueError('Declared exit completed close is malformed')
        if value.quote_age_us is not None and type(value.quote_age_us) is not int:
            raise ValueError('Declared exit quote age is malformed')
        if any(x is not None and (type(x) not in (int, float) or not isfinite(x))
                for x in (value.macd_line, value.macd_signal, value.bid, value.ask)):
            raise ValueError('Declared exit completed scalar is malformed')
        if (context.first_held_boundary_ms is None
                or (value.boundary_ms,value.first_held_boundary_ms,value.reference_ask,value.initial_stop,
                    value.position_quantity,value.pending_exit)
                != (context.boundary_ms,context.first_held_boundary_ms,context.source.reference_ask,
                    context.source.initial_stop,context.financial.position_quantity,context.financial.pending_exit)):
            raise ValueError('Declared exit producer input changed original risk or financial context')
        if len(self.candles) > 4:
            raise ValueError('Declared exit candle coverage is malformed')
        for candle in self.candles:
            _clock(candle.boundary_ms, 5000)
            if (candle.boundary_ms > context.boundary_ms or type(candle.trade_count) is not int
                    or not 0 <= candle.trade_count <= (1 << 64) - 1):
                raise ValueError('Declared exit candle source is malformed or future')
        if self.ten:
            _clock(self.ten.get('boundary_ms'),10_000)
            if self.ten['boundary_ms'] > context.boundary_ms:
                raise ValueError('Declared exit10s source is future')
        prior = self.prior_arm
        if prior is not None:
            if type(prior) is not DeclaredProfitArmReference:
                raise ValueError('Declared exit has foreign arm reference')
            prior.__post_init__()
            candidate=prior.candidate
            _clock(candidate.boundary_ms); _clock(candidate.first_held_boundary_ms)
            if ((prior.run_id,prior.source_token,candidate.account_id,candidate.assignment_id,candidate.ticker)
                    != (context.run_id,context.source_token,context.source.account_id,
                        context.source.assignment_id,context.source.ticker)
                    or candidate.boundary_ms >= context.boundary_ms):
                raise ValueError('Declared exit arm lacks prior own scope')
        if context.policy.liquidation_due(context.boundary_ms):
            raise ValueError('Declared session liquidation precedes conditional management')
        return declared_management_exit(value,policy=context.policy,
            prior_arm=None if prior is None else prior.candidate,ten=self.ten,candles=self.candles)


@dataclass(frozen=True, slots=True)
class DeclaredExitCommand:
    context: DeclaredManagementContext
    inputs: DeclaredExitInputs
    kind: str
    witness: object

    def replay(self):
        if type(self.context) is not DeclaredManagementContext or type(self.inputs) is not DeclaredExitInputs:
            raise ValueError('Declared exit command has foreign envelope')
        expected=self.inputs.replay(self.context)
        if type(self.kind) is not str or expected is None or not _equal(expected, (self.kind,self.witness)):
            raise ValueError('Declared exit command changed inherited priority or witness')
        return expected


@dataclass(frozen=True, slots=True)
class DeclaredProtectionInputs:
    previous: ProtectionState
    now_ms: int
    bid: float
    ask: float
    tick: float
    low_boundary_ms: int | None
    low_int: int | None
    low_price_valid: bool
    low_extremes_valid: bool
    breaks: tuple[ResistanceBreak, ...]
    overhead_levels: tuple
    price_bearing_bar: bool

    def __post_init__(self):
        if (type(self.previous) is not ProtectionState or type(self.breaks) is not tuple
                or type(self.overhead_levels) is not tuple or any(type(x) is not bool for x in
                    (self.low_price_valid,self.low_extremes_valid,self.price_bearing_bar))
                or any(type(x) not in (int,float) or not isfinite(x) or x <= 0 for x in (self.bid,self.ask,self.tick))
                or any(type(x) is not ResistanceBreak for x in self.breaks)):
            raise ValueError('Declared protection command lacks exact reducer inputs')
        _clock(self.now_ms); _clock(self.previous.boundary_ms)
        if self.low_boundary_ms is not None:
            _clock(self.low_boundary_ms, 30_000)
            if self.low_boundary_ms > self.now_ms:
                raise ValueError('Declared protection low source is future')
        if self.low_int is not None and (type(self.low_int) is not int or self.low_int <= 0):
            raise ValueError('Declared protection low is malformed')
        for row in self.breaks:
            _clock(row.completed_boundary_ms)
            if row.completed_boundary_ms > self.now_ms:
                raise ValueError('Declared protection break is future')
        if self.previous.boundary_ms >= self.now_ms:
            raise ValueError('Declared protection prior state is not prior')
        object.__setattr__(self,'breaks',tuple(ResistanceBreak(x.completed_boundary_ms,_freeze(x.level)) for x in self.breaks))
        object.__setattr__(self,'overhead_levels',_freeze(self.overhead_levels))

    def replay(self, context, exits):
        self.__post_init__()
        if (exits.replay(context) is not None or self.now_ms != context.boundary_ms
                or (self.bid,self.ask) != (exits.completed.bid,exits.completed.ask)
                or context.financial.pending_exit):
            raise ValueError('Declared protection is after a higher-priority exit or foreign quote')
        flags=context.policy.capabilities.payload()['inherited']['flags']
        return advance_protection(self.previous,now_ms=self.now_ms,bid=self.bid,ask=self.ask,tick=self.tick,
            low_boundary_ms=self.low_boundary_ms,low_int=self.low_int,low_price_valid=self.low_price_valid,
            low_extremes_valid=self.low_extremes_valid,breaks=self.breaks,overhead_levels=self.overhead_levels,
            price_bearing_bar=self.price_bearing_bar,allows_completed_30s_trailing=flags['allows_completed_30s_trailing'],
            allows_target_escalation=flags['allows_target_escalation'])


@dataclass(frozen=True, slots=True)
class DeclaredProtectionCommand:
    context: DeclaredManagementContext
    exit_inputs: DeclaredExitInputs
    inputs: DeclaredProtectionInputs
    transition: ProtectionTransition

    def __post_init__(self):
        if type(self.transition) is not ProtectionTransition:
            raise ValueError('Declared protection transition is foreign')
        object.__setattr__(self, 'transition', replace(self.transition,
            stop_amendment=_freeze(self.transition.stop_amendment),
            target_amendment=_freeze(self.transition.target_amendment)))

    def replay(self):
        self.__post_init__()
        if (type(self.context) is not DeclaredManagementContext or type(self.exit_inputs) is not DeclaredExitInputs
                or type(self.inputs) is not DeclaredProtectionInputs or type(self.transition) is not ProtectionTransition):
            raise ValueError('Declared protection command has foreign envelope')
        expected=self.inputs.replay(self.context,self.exit_inputs)
        expected = replace(expected, stop_amendment=_freeze(expected.stop_amendment),
            target_amendment=_freeze(expected.target_amendment))
        if not _equal(expected, self.transition):
            raise ValueError('Declared protection transition differs from independent reducer')
        return expected


@dataclass(frozen=True, slots=True)
class DeclaredSessionCommand:
    context: DeclaredManagementContext
    resolutions: Mapping

    def __post_init__(self):
        if type(self.context) is not DeclaredManagementContext or not isinstance(self.resolutions,Mapping):
            raise ValueError('Declared session command has foreign context')
        object.__setattr__(self,'resolutions',_freeze(self.resolutions))

    def replay(self):
        self.context.__post_init__();self.__post_init__()
        if not self.context.policy.liquidation_due(self.context.boundary_ms):
            raise ValueError('Declared session command is outside liquidation')
        for resolution,row in self.resolutions.items():
            if type(resolution) is not int or resolution < 100 or resolution % 100 or not isinstance(row,Mapping):
                raise ValueError('Declared session producer rows malformed')
            if 'boundary_ms' in row:
                _clock(row['boundary_ms'],resolution)
                if row['boundary_ms'] > self.context.boundary_ms:
                    raise ValueError('Declared session producer row is future')
        return self.context.boundary_ms
