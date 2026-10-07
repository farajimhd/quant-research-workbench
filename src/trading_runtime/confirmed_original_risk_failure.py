"""Declared two-completed-bucket original-risk extension; no order authority."""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from datetime import date
from uuid import UUID
import re

from .strategy_followthrough_failure import FollowThroughFailure, FollowThroughFailureInput, followthrough_failure

CONFIRMED_ORIGINAL_RISK_RULE = 'held-consecutive-quarter-original-risk-failure@1'
CONFIRMED_ORIGINAL_RISK_INPUT = 'declared-consecutive-original-risk-source@1'
INHERITED_ORIGINAL_RISK_RULE = 'strategy-thirty-zero-regime-original-risk-failure-v1'


@dataclass(frozen=True, slots=True)
class ConfirmedOriginalRiskPolicy:
    policy_id: str = CONFIRMED_ORIGINAL_RISK_RULE
    original_risk_fraction: tuple[int, int] = (1, 4)
    completed_bucket_ms: int = 5000
    consecutive_buckets: int = 2
    quote_max_age_us: int = 1_000_000

    def __post_init__(self):
        if (type(self.policy_id) is not str or self.policy_id != CONFIRMED_ORIGINAL_RISK_RULE
                or type(self.original_risk_fraction) is not tuple
                or self.original_risk_fraction != (1, 4)
                or any(type(v) is not int for v in self.original_risk_fraction)
                or any(type(v) is not int for v in (self.completed_bucket_ms, self.consecutive_buckets, self.quote_max_age_us))
                or (self.completed_bucket_ms, self.consecutive_buckets, self.quote_max_age_us) != (5000, 2, 1_000_000)):
            raise ValueError('Confirmed original risk requires its exact typed two-bucket declaration')

    def payload(self):
        return dict(policy_id=self.policy_id, original_risk_fraction=self.original_risk_fraction,
                    completed_bucket_ms=self.completed_bucket_ms, consecutive_buckets=self.consecutive_buckets,
                    quote_max_age_us=self.quote_max_age_us, priority='inherited_exits_first',
                    threshold='original_ask_minus_quarter_original_risk',
                    close='both_completed_closes_at_or_below_threshold',
                    momentum='both_completed_macd_lines_strictly_below_signals',
                    held_fence='both_buckets_wholly_post_held',
                    source='same_certified_session_build_bars_indicators_attempts',
                    missing='no_extension', diagnostic='semantic_rule_and_exact_completed_witnesses@1')


@dataclass(frozen=True, slots=True)
class CompletedRiskBucket:
    boundary_ms: int
    close_int: int | None
    price_valid: bool
    macd_line: float | None
    macd_signal: float | None
    source_build_id: str
    source_market_plan_token: str
    source_bars_attempt_id: str
    source_indicators_attempt_id: str
    session_date: str
    ticker: str
    source_liquidity_attempt_id: str

    def validate_source(self):
        if (any(type(v) is not str or not v for v in (self.source_build_id,
                self.source_market_plan_token, self.source_bars_attempt_id,
                self.source_indicators_attempt_id, self.session_date, self.ticker,
                self.source_liquidity_attempt_id))
                or re.fullmatch('[0-9a-f]{64}', self.source_market_plan_token) is None):
            raise ValueError('Completed risk bucket lacks exact source identity')
        try:
            if date.fromisoformat(self.session_date).isoformat() != self.session_date:
                raise ValueError('Noncanonical source date')
            for value in (self.source_bars_attempt_id, self.source_indicators_attempt_id,
                          self.source_liquidity_attempt_id):
                if str(UUID(value)) != value:
                    raise ValueError('Noncanonical producer attempt')
        except (ValueError, TypeError) as exc:
            raise ValueError('Completed risk bucket has malformed date or producer attempt') from exc


@dataclass(frozen=True, slots=True)
class ConfirmedOriginalRiskWitness:
    current: FollowThroughFailure
    prior: CompletedRiskBucket
    newest: CompletedRiskBucket
    semantic_rule: str = CONFIRMED_ORIGINAL_RISK_RULE


@dataclass(frozen=True, slots=True)
class OriginalRiskDecisionDiagnostic:
    """Selected firing rule, distinct from the unchanged historical exit reason."""
    current: FollowThroughFailure
    newest: CompletedRiskBucket
    prior: CompletedRiskBucket | None
    semantic_rule: str
    checkpoint: object = None


def validate_decision_diagnostic(diagnostic, *, policy, premarket_policy=None):
    if (type(diagnostic) is not OriginalRiskDecisionDiagnostic
            or type(diagnostic.current) is not FollowThroughFailure
            or type(diagnostic.newest) is not CompletedRiskBucket
            or type(diagnostic.semantic_rule) is not str):
        raise ValueError('Original-risk diagnostic requires exact typed firing authority')
    diagnostic.newest.validate_source()
    witness = diagnostic.current
    newest = diagnostic.newest
    if (newest.boundary_ms != witness.boundary_ms
            or (newest.close_int,newest.price_valid,newest.macd_line,newest.macd_signal)
               != (witness.completed_close_int,True,witness.macd_line,witness.macd_signal)):
        raise ValueError('Diagnostic newest completed facts differ from exit witness')
    value = FollowThroughFailureInput(witness.boundary_ms,witness.first_held_boundary_ms,
        witness.reference_ask,witness.initial_stop,newest.boundary_ms,newest.close_int,
        newest.price_valid,newest.macd_line,newest.macd_signal,witness.bid,witness.ask,
        witness.quote_age_us,1.,False)
    from .strategy_zero_regime_risk_failure import zero_regime_risk_failure
    inherited = zero_regime_risk_failure(value)
    from .premarket_confirmed_original_risk import (
        PREMARKET_CONFIRMED_RISK_RULE, replacement_stage, premarket_confirmed_original_risk_failure)
    replaced = premarket_policy is not None and replacement_stage(value,policy=premarket_policy)
    if diagnostic.semantic_rule == PREMARKET_CONFIRMED_RISK_RULE:
        if premarket_policy is None or not replaced:
            raise ValueError('PM replacement diagnostic lacks exact selected stage')
        actual=premarket_confirmed_original_risk_failure(value,prior=diagnostic.prior,newest=newest,policy=premarket_policy)
        if actual is None or actual.current!=witness:
            raise ValueError('PM replacement diagnostic lacks its exact consecutive witnesses')
        return diagnostic
    if replaced:
        raise ValueError('Replaced PM stage cannot claim inherited or extension firing authority')
    if diagnostic.semantic_rule == INHERITED_ORIGINAL_RISK_RULE:
        if diagnostic.prior is not None or inherited != witness:
            raise ValueError('Inherited firing diagnostic differs from exact inherited rule')
    elif diagnostic.semantic_rule == CONFIRMED_ORIGINAL_RISK_RULE:
        if inherited is not None:
            raise ValueError('Confirmed diagnostic cannot supersede inherited exit priority')
        actual = confirmed_original_risk_failure(value,prior=diagnostic.prior,newest=newest,policy=policy)
        if actual is None or actual.current != witness:
            raise ValueError('Confirmed firing diagnostic lacks its exact consecutive witness')
    else:
        raise ValueError('Foreign semantic firing rule')
    return diagnostic


def confirmed_original_risk_failure(value, *, prior, newest, policy):
    """Consume exact producer observations, with original position authority."""
    if type(policy) is not ConfirmedOriginalRiskPolicy:
        raise ValueError('Confirmed original risk requires exact policy')
    policy.__post_init__()
    followthrough_failure(value)  # inherited malformed-position validation
    if prior is None or newest is None:
        return None
    if type(prior) is not CompletedRiskBucket or type(newest) is not CompletedRiskBucket:
        raise ValueError('Confirmed original risk requires typed completed source buckets')
    for bucket in (prior, newest):
        bucket.validate_source()
    if any(type(b.boundary_ms) is not int or not 0 < b.boundary_ms <= 57_600_000
           or b.boundary_ms % 5000 for b in (prior, newest)):
        return None
    identity = ('source_build_id', 'source_market_plan_token', 'source_bars_attempt_id',
                'source_indicators_attempt_id', 'session_date', 'ticker', 'source_liquidity_attempt_id')
    if (any(type(getattr(b, k)) is not str or not getattr(b, k) for b in (prior, newest) for k in identity)
            or any(getattr(prior, k) != getattr(newest, k) for k in identity)):
        return None
    pm = 0 < value.first_held_boundary_ms < 19_800_000 and value.boundary_ms <= 19_800_000
    ah = 43_200_000 <= value.first_held_boundary_ms < 57_600_000 and value.boundary_ms <= 57_600_000
    if (not (pm or ah) or value.pending_exit or value.position_quantity == 0
            or value.boundary_ms % 5000 or newest.boundary_ms != value.boundary_ms
            or prior.boundary_ms != newest.boundary_ms - 5000
            or prior.boundary_ms - 5000 < value.first_held_boundary_ms
            or value.completed_five_second_boundary_ms != newest.boundary_ms
            or (value.completed_five_second_close_int, value.price_valid, value.macd_line, value.macd_signal)
               != (newest.close_int, newest.price_valid, newest.macd_line, newest.macd_signal)
            or any(type(b.boundary_ms) is not int or type(b.price_valid) is not bool or not b.price_valid
                   or type(b.close_int) is not int or not 0 < b.close_int < 2**64
                   or any(type(x) not in (int, float) or not isfinite(x) for x in (b.macd_line, b.macd_signal))
                   or b.macd_line >= b.macd_signal for b in (prior, newest))
            or any(type(x) not in (int, float) or not isfinite(x) for x in (value.bid, value.ask))
            or not 0 < value.bid <= value.ask or type(value.quote_age_us) is not int
            or not 0 <= value.quote_age_us <= policy.quote_max_age_us):
        return None
    ask = Fraction(str(value.reference_ask))
    threshold = ask - (ask - Fraction(str(value.initial_stop))) / 4
    if any(b.close_int > threshold * 10_000 for b in (prior, newest)) or Fraction(str(value.bid)) > threshold:
        return None
    current = FollowThroughFailure(value.boundary_ms, value.first_held_boundary_ms,
        value.reference_ask, value.initial_stop, newest.close_int, float(newest.macd_line),
        float(newest.macd_signal), float(value.bid), float(value.ask), value.quote_age_us)
    return ConfirmedOriginalRiskWitness(current, prior, newest)
