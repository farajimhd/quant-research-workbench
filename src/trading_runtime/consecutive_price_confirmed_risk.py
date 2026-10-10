"""Pure consecutive price-risk confirmation over certified completed facts.

This adds no registration, source approval, order submission or new release.
The owning declared strategy must select the policy and preserve exit priority.
"""
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from .confirmed_original_risk_failure import (
    CompletedRiskBucket, OriginalRiskDecisionDiagnostic, INHERITED_ORIGINAL_RISK_RULE,
)
from .journal_contract import canonical_json
from .price_confirmed_original_risk import (
    PriceConfirmedOriginalRiskPolicy, price_confirmed_original_risk_failure,
)
from .strategy_followthrough_failure import FollowThroughFailure

RULE = 'consecutive-price-confirmed-original-risk-exit@1'
INPUT = 'declared-consecutive-price-confirmed-original-risk-source@1'
POLICY_KEY = 'consecutive_price_confirmed_original_risk_policy'
ENTRY_COST_CAPABILITY_RULE = 'original-risk-and-entry-cost-journal-capability@1'
SOURCE_FIELDS = (
    'source_build_id', 'source_market_plan_token', 'source_bars_attempt_id',
    'source_indicators_attempt_id', 'session_date', 'ticker',
    'source_liquidity_attempt_id',
)


@dataclass(frozen=True, slots=True)
class ConsecutivePriceRiskPolicy:
    price_policy: PriceConfirmedOriginalRiskPolicy
    completed_bucket_ms: int = 5000
    consecutive_buckets: int = 2
    entry_cost_capability: bool = False

    def __post_init__(self):
        if (type(self.price_policy) is not PriceConfirmedOriginalRiskPolicy
                or type(self.completed_bucket_ms) is not int
                or type(self.consecutive_buckets) is not int
                or type(self.entry_cost_capability) is not bool
                or (self.completed_bucket_ms, self.consecutive_buckets) != (5000, 2)):
            raise ValueError('Exact certified completed-pair policy required')
        self.price_policy.__post_init__()

    def payload(self):
        self.__post_init__()
        result = dict(rule=RULE, price_policy=self.price_policy.payload(),
            completed_bucket_ms=self.completed_bucket_ms,
            consecutive_buckets=self.consecutive_buckets,
            confirmation='both_completed_closes_and_current_fresh_bid',
            source='same_certified_session_build_bars_indicators_liquidity_attempts',
            held_fence='both_candles_wholly_after_first_held',
            priority='inherited_exits_first', missing='no_extension')
        if self.entry_cost_capability:
            result['entry_cost_capability'] = ENTRY_COST_CAPABILITY_RULE
        return result


@dataclass(frozen=True, slots=True)
class ConsecutivePriceRiskWitness:
    current: FollowThroughFailure
    prior: CompletedRiskBucket
    newest: CompletedRiskBucket
    semantic_rule: str = RULE


def parse_consecutive_price_risk_policy(release, policies):
    """The rule, input contract and normalized policy must be selected together."""
    selected = (RULE in release.rule_set_contracts, INPUT in release.input_contracts,
                POLICY_KEY in policies)
    if not any(selected):
        if ENTRY_COST_CAPABILITY_RULE in release.rule_set_contracts:
            raise ValueError('Combined capability requires consecutive price-risk declaration')
        return None
    if not all(selected):
        raise ValueError('Consecutive price-risk declaration is incomplete')
    payload = policies[POLICY_KEY]
    if type(payload) is not dict or type(payload.get('price_policy')) is not dict:
        raise ValueError('Normalized consecutive price-risk policy required')
    price = payload['price_policy']
    def fraction(name):
        value = price[name]
        if value is None:
            return None
        if type(value) is not list or len(value) != 2:
            raise ValueError('Normalized rational price-risk fraction required')
        return tuple(value)
    try:
        result = ConsecutivePriceRiskPolicy(PriceConfirmedOriginalRiskPolicy(
            fraction('premarket_fraction'), fraction('afterhours_fraction'),
            price['eligibility_ms'], price['quote_max_age_us']),
            payload['completed_bucket_ms'], payload['consecutive_buckets'],
            payload.get('entry_cost_capability') == ENTRY_COST_CAPABILITY_RULE)
    except (KeyError, TypeError) as exc:
        raise ValueError('Incomplete consecutive price-risk policy') from exc
    if canonical_json(payload) != canonical_json(result.payload()):
        raise ValueError('Consecutive price-risk semantics changed')
    if release.rule_set_contracts.count(ENTRY_COST_CAPABILITY_RULE) != int(result.entry_cost_capability):
        raise ValueError('Combined entry-cost capability declaration differs')
    return result


def consecutive_price_risk_failure(value, *, prior, newest, policy):
    """No future, partial, missing or cross-authority candle can confirm risk."""
    if type(policy) is not ConsecutivePriceRiskPolicy:
        raise ValueError('Exact declared consecutive price-risk policy required')
    policy.__post_init__()
    current = price_confirmed_original_risk_failure(value, policy=policy.price_policy)
    if prior is None or newest is None:
        return None
    if type(prior) is not CompletedRiskBucket or type(newest) is not CompletedRiskBucket:
        raise ValueError('Exact producer completed-risk observations required')
    prior.validate_source()
    newest.validate_source()
    if any(getattr(prior, name) != getattr(newest, name) for name in SOURCE_FIELDS):
        raise ValueError('Consecutive risk observations changed source ownership')
    if (type(newest.close_int) is not int or type(newest.price_valid) is not bool
            or newest.boundary_ms != value.boundary_ms
            or (newest.close_int, newest.price_valid, newest.macd_line, newest.macd_signal)
            != (value.completed_five_second_close_int, value.price_valid,
                value.macd_line, value.macd_signal)):
        raise ValueError('Newest completed observation differs from current witness')
    if (type(prior.boundary_ms) is not int or type(newest.boundary_ms) is not int
            or not 0 < prior.boundary_ms < newest.boundary_ms <= 57_600_000
            or prior.boundary_ms % policy.completed_bucket_ms
            or newest.boundary_ms % policy.completed_bucket_ms
            or newest.boundary_ms - prior.boundary_ms != policy.completed_bucket_ms
            or prior.boundary_ms - policy.completed_bucket_ms < value.first_held_boundary_ms
            or current is None
            or prior.price_valid is not True
            or type(prior.close_int) is not int or prior.close_int <= 0
            or any(type(x) not in (int, float) or not isfinite(x)
                   for x in (prior.macd_line, prior.macd_signal))):
        return None
    fraction = (policy.price_policy.premarket_fraction if value.boundary_ms <= 19_800_000
                else policy.price_policy.afterhours_fraction)
    threshold = Fraction(str(value.reference_ask)) - Fraction(*fraction) * (
        Fraction(str(value.reference_ask)) - Fraction(str(value.initial_stop)))
    if Fraction(prior.close_int, 10000) > threshold:
        return None
    return ConsecutivePriceRiskWitness(current, prior, newest)


def validate_consecutive_price_risk_diagnostic(diagnostic, *, policy, inherited_early_policy=None):
    """Keep inherited priority and both exact producer witnesses for recovery."""
    from .strategy_followthrough_failure import FollowThroughFailureInput
    from .strategy_zero_regime_risk_failure import zero_regime_risk_failure
    if (type(policy) is not ConsecutivePriceRiskPolicy
            or type(diagnostic) is not OriginalRiskDecisionDiagnostic
            or type(diagnostic.current) is not FollowThroughFailure
            or type(diagnostic.newest) is not CompletedRiskBucket):
        raise ValueError('Exact consecutive price-risk diagnostic required')
    policy.__post_init__()
    newest, witness = diagnostic.newest, diagnostic.current
    newest.validate_source()
    if ((newest.boundary_ms, newest.close_int, newest.price_valid,
         newest.macd_line, newest.macd_signal)
            != (witness.boundary_ms, witness.completed_close_int, True,
                witness.macd_line, witness.macd_signal)):
        raise ValueError('Diagnostic newest source differs from current witness')
    value = FollowThroughFailureInput(witness.boundary_ms, witness.first_held_boundary_ms,
        witness.reference_ask, witness.initial_stop, newest.boundary_ms, newest.close_int,
        newest.price_valid, newest.macd_line, newest.macd_signal,
        witness.bid, witness.ask, witness.quote_age_us, 1., False)
    inherited = zero_regime_risk_failure(value)
    from .early_original_risk_failure import early_original_risk_failure
    early = (early_original_risk_failure(value, policy=inherited_early_policy)
             if inherited_early_policy is not None else None)
    if diagnostic.semantic_rule == INHERITED_ORIGINAL_RISK_RULE:
        if diagnostic.prior is not None or inherited != witness:
            raise ValueError('Inherited diagnostic must preserve its exact firing rule')
    elif diagnostic.semantic_rule == RULE:
        if inherited is not None or early is not None:
            raise ValueError('Consecutive diagnostic cannot supersede inherited priority')
        actual = consecutive_price_risk_failure(value, prior=diagnostic.prior,
            newest=newest, policy=policy)
        if actual is None or actual.current != witness:
            raise ValueError('Consecutive diagnostic lacks both exact completed witnesses')
    elif inherited_early_policy is not None and diagnostic.semantic_rule == inherited_early_policy.policy_id:
        if inherited is not None or diagnostic.prior is not None or early != witness:
            raise ValueError('Inherited early-risk diagnostic differs from exact selected rule')
    else:
        raise ValueError('Foreign consecutive price-risk semantic rule')
    return diagnostic
