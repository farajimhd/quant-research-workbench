"""Declared replacement of only the first-minute PM single-bucket stage.

Consumes existing certified completed-risk observations; no market, order or
financial authority. Late-PM and AH use the unchanged inherited reducers.
"""
from dataclasses import dataclass, replace
from math import isfinite
from .confirmed_original_risk_failure import CompletedRiskBucket, ConfirmedOriginalRiskWitness
from .strategy_zero_regime_risk_failure import zero_regime_risk_failure
from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS
from .strategy_followthrough_failure import followthrough_failure

PREMARKET_CONFIRMED_RISK_RULE = 'premarket-first-minute-consecutive-quarter-original-risk-failure@1'
PREMARKET_CONFIRMED_RISK_INPUT = 'declared-premarket-first-minute-consecutive-original-risk-source@1'

@dataclass(frozen=True, slots=True)
class PremarketConfirmedOriginalRiskPolicy:
    policy_id: str = PREMARKET_CONFIRMED_RISK_RULE
    original_risk_fraction: tuple[int,int] = (1,4)
    completed_bucket_ms: int = 5000
    consecutive_buckets: int = 2
    maximum_held_age_ms: int = EARLY_FAILURE_WINDOW_MS
    quote_max_age_us: int = 1000000
    threshold_arithmetic: str = "inherited_float64_quarter"
    session_start_ms: int = 0
    session_end_ms: int = 19800000

    def __post_init__(self):
        if (type(self.threshold_arithmetic) is not str or self.threshold_arithmetic != "inherited_float64_quarter"
            or type(self.policy_id) is not str or self.policy_id!=PREMARKET_CONFIRMED_RISK_RULE
            or type(self.original_risk_fraction) is not tuple or self.original_risk_fraction!=(1,4)
            or any(type(v) is not int for v in self.original_risk_fraction)
            or any(type(getattr(self,k)) is not int for k in ('completed_bucket_ms','consecutive_buckets','maximum_held_age_ms','quote_max_age_us','session_start_ms','session_end_ms'))
            or (self.completed_bucket_ms,self.consecutive_buckets,self.maximum_held_age_ms,self.quote_max_age_us,self.session_start_ms,self.session_end_ms)!=(5000,2,EARLY_FAILURE_WINDOW_MS,1000000,0,19800000)):
            raise ValueError('PM replacement requires exact typed first-minute two-bucket policy')

    def payload(self):
        return dict(policy_id=self.policy_id, original_risk_fraction=list(self.original_risk_fraction),
            completed_bucket_ms=self.completed_bucket_ms,consecutive_buckets=self.consecutive_buckets,
            maximum_held_age_ms=self.maximum_held_age_ms,maximum_held_age_inclusive=True,
            quote_max_age_us=self.quote_max_age_us,session_start_ms=self.session_start_ms,session_end_ms=self.session_end_ms,
            scope='replace_only_inherited_first_minute_premarket',priority='same_inherited_failure_stage',
            threshold='original_ask_minus_quarter_original_risk',threshold_arithmetic=self.threshold_arithmetic,close='both_completed_closes_at_or_below_threshold',
            momentum='both_completed_macd_lines_strictly_below_signals',held_fence='both_buckets_wholly_post_held',
            source='same_certified_session_build_bars_indicators_liquidity_attempts',
            missing='no_replacement_exit_no_single_bucket_fallback',diagnostic='exact_selected_replacement_rule_and_two_buckets@1')

def replacement_stage(value, *, policy):
    if type(policy) is not PremarketConfirmedOriginalRiskPolicy:
        raise ValueError('PM replacement requires exact selected policy')
    policy.__post_init__()
    followthrough_failure(value)  # exact inherited malformed-position validation
    return (type(value.first_held_boundary_ms) is int and type(value.boundary_ms) is int
        and policy.session_start_ms<value.first_held_boundary_ms<policy.session_end_ms
        and value.first_held_boundary_ms<=value.boundary_ms<=policy.session_end_ms
        and value.boundary_ms-value.first_held_boundary_ms<=policy.maximum_held_age_ms)

def premarket_confirmed_original_risk_failure(value, *, prior, newest, policy):
    if not replacement_stage(value,policy=policy):
        return None
    current=zero_regime_risk_failure(value)
    if current is None or prior is None or newest is None:
        return None
    if type(prior) is not CompletedRiskBucket or type(newest) is not CompletedRiskBucket:
        raise ValueError('PM confirmation requires exact typed completed source buckets')
    identity=('source_build_id','source_market_plan_token','source_bars_attempt_id',
              'source_indicators_attempt_id','source_liquidity_attempt_id','session_date','ticker')
    for bucket in (prior,newest):
        bucket.validate_source()
        if (type(bucket.boundary_ms) is not int or not 0<bucket.boundary_ms<=policy.session_end_ms
            or bucket.boundary_ms%policy.completed_bucket_ms or type(bucket.price_valid) is not bool
            or not bucket.price_valid or type(bucket.close_int) is not int or not 0<bucket.close_int<2**64
            or any(type(v) not in (int,float) or not isfinite(v) for v in (bucket.macd_line,bucket.macd_signal))
            or bucket.macd_line>=bucket.macd_signal):
            return None
    if (any(getattr(prior,k)!=getattr(newest,k) for k in identity)
        or newest.boundary_ms!=value.boundary_ms
        or prior.boundary_ms!=newest.boundary_ms-policy.completed_bucket_ms
        or prior.boundary_ms-policy.completed_bucket_ms<value.first_held_boundary_ms
        or (newest.close_int,newest.price_valid,newest.macd_line,newest.macd_signal)
           !=(value.completed_five_second_close_int,value.price_valid,value.macd_line,value.macd_signal)):
        return None
    # Preserve the selected inherited first-minute Float64 operation order.
    # The prior bar has no manufactured quote: only its close/momentum is used.
    threshold=(3*value.reference_ask+value.initial_stop)/4
    if not isfinite(threshold):
        raise ValueError('PM confirmation inherited Float64 threshold overflowed')
    if prior.close_int>threshold*10000:
        return None
    return ConfirmedOriginalRiskWitness(current,prior,newest,policy.policy_id)
