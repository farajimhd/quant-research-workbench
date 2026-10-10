"""Declared price-only extension using the original causal proposal risk.

Consumes the existing certified completed-five-second input, not an average
fill or reconstructed research price. This predicate has no order authority.
"""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from .strategy_followthrough_failure import (
    FollowThroughFailure, FollowThroughFailureInput, followthrough_failure,
)
from .journal_contract import canonical_json

INPUT = 'declared-price-confirmed-original-risk-source@1'
RULE = 'price-confirmed-original-risk-exit@1'
POLICY_KEY = 'price_confirmed_original_risk_policy'


@dataclass(frozen=True, slots=True)
class PriceConfirmedOriginalRiskPolicy:
    premarket_fraction: tuple[int, int] | None
    afterhours_fraction: tuple[int, int] | None
    eligibility_ms: int
    quote_max_age_us: int

    def __post_init__(self):
        if (type(self.eligibility_ms) is not int
                or not 5000 <= self.eligibility_ms <= 57_600_000
                or self.eligibility_ms % 100
                or type(self.quote_max_age_us) is not int
                or not 0 <= self.quote_max_age_us <= 1_000_000
                or self.premarket_fraction is None and self.afterhours_fraction is None):
            raise ValueError('Complete bounded original-risk policy required')
        for fraction in (self.premarket_fraction, self.afterhours_fraction):
            if fraction is not None and (type(fraction) is not tuple or len(fraction) != 2
                    or any(type(x) is not int for x in fraction)
                    or not 0 < fraction[0] < fraction[1] <= 10000):
                raise ValueError('Exact positive original-risk fraction required')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, premarket_fraction=self.premarket_fraction,
            afterhours_fraction=self.afterhours_fraction, eligibility_ms=self.eligibility_ms,
            quote_max_age_us=self.quote_max_age_us, reference='original_proposal_ask',
            stop='original_fixed_stop', age_origin='native_first_completed_held_bucket',
            evidence_resolution_ms=5000, evidence='completed_candle_wholly_after_first_held',
            confirmation='completed_close_and_fresh_bid_at_or_below_exact_rational_threshold',
            momentum='finite_certified_values_retained_in_witness_without_direction_veto',
            priority='inherited_exits_first', pending_exit='no_duplicate_exit',
            missing='no_synthetic_observations')


def parse_price_confirmed_original_risk_policy(release, policies):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease or type(policies) is not dict:
        raise ValueError('Exact release and policy payload required')
    release.verify()
    selected = release.rule_set_contracts.count(RULE)
    source = release.input_contracts.count(INPUT)
    if not selected and not source and POLICY_KEY not in policies:
        return None
    payload = policies.get(POLICY_KEY)
    if selected != 1 or source != 1 or type(payload) is not dict:
        raise ValueError('Risk extension requires paired rule, source and payload')
    def fraction(value):
        if value is None:
            return None
        if type(value) is not list or len(value) != 2:
            raise ValueError('Normalized JSON risk fractions required')
        return tuple(value)
    try:
        result = PriceConfirmedOriginalRiskPolicy(fraction(payload['premarket_fraction']),
            fraction(payload['afterhours_fraction']), payload['eligibility_ms'],
            payload['quote_max_age_us'])
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete original-risk policy') from exc
    if canonical_json(payload) != canonical_json(result.payload()):
        raise ValueError('Original-risk price, clock or source semantics differ')
    return result


def price_confirmed_original_risk_failure(value, *, policy):
    if type(policy) is not PriceConfirmedOriginalRiskPolicy:
        raise ValueError('Exact declared original-risk policy required')
    policy.__post_init__()
    # Retain malformed ownership rejection independently of the old rule's
    # momentum predicate; its returned witness is deliberately not eligibility.
    followthrough_failure(value)
    pm = 0 < value.boundary_ms <= 19_800_000
    ah = 43_200_000 < value.boundary_ms <= 57_600_000
    fraction = policy.premarket_fraction if pm else policy.afterhours_fraction if ah else None
    if (fraction is None or value.position_quantity == 0 or value.pending_exit
            or value.boundary_ms % 5000
            or value.boundary_ms - value.first_held_boundary_ms > policy.eligibility_ms
            or type(value.completed_five_second_boundary_ms) is not int
            or value.completed_five_second_boundary_ms != value.boundary_ms
            or value.boundary_ms - 5000 < value.first_held_boundary_ms
            or not value.price_valid
            or type(value.completed_five_second_close_int) is not int
            or value.completed_five_second_close_int <= 0
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (value.macd_line, value.macd_signal, value.bid, value.ask))
            or not 0 < value.bid <= value.ask
            or type(value.quote_age_us) is not int
            or not 0 <= value.quote_age_us <= policy.quote_max_age_us):
        return None
    reference = Fraction(str(value.reference_ask))
    threshold = reference - Fraction(*fraction) * (reference - Fraction(str(value.initial_stop)))
    if (Fraction(value.completed_five_second_close_int, 10000) > threshold
            or Fraction(str(value.bid)) > threshold):
        return None
    return FollowThroughFailure(value.boundary_ms, value.first_held_boundary_ms,
        value.reference_ask, value.initial_stop, value.completed_five_second_close_int,
        float(value.macd_line), float(value.macd_signal), float(value.bid), float(value.ask),
        value.quote_age_us)
