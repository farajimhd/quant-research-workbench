"""Typed early failure selected by declared input and policy rule, never number."""
from .early_original_risk_failure import EarlyOriginalRiskPolicy
from .journal_contract import canonical_json

INPUT_CONTRACT = 'declared-first-minute-original-risk-failure-source@1'
POLICY_KEY = 'early_original_risk_failure_policy'


def parse_declared_early_original_risk_policy(release, policies):
    from .strategy_registry import NumberedStrategyRelease
    if type(release) is not NumberedStrategyRelease or type(policies) is not dict:
        raise ValueError('Exact release and declared policies required')
    release.verify()
    count = release.input_contracts.count(INPUT_CONTRACT)
    if POLICY_KEY not in policies and count == 0:
        return None
    payload = policies.get(POLICY_KEY)
    if count != 1 or type(payload) is not dict:
        raise ValueError('Early failure needs one declared input and complete policy')
    def fraction(value):
        if value is None:
            return None
        if type(value) is not list or len(value) != 2:
            raise ValueError('Early failure fractions need exact normalized pairs')
        return tuple(value)
    bounds = payload.get('signal_reference_fraction_bounds')
    if bounds is not None:
        if type(bounds) is not list or len(bounds) != 2:
            raise ValueError('Early failure signal bounds need exact normalized pairs')
        bounds = tuple(fraction(v) for v in bounds)
    try:
        policy = EarlyOriginalRiskPolicy(
            payload['policy_id'], fraction(payload['premarket_fraction']),
            fraction(payload['afterhours_fraction']), eligibility_ms=payload['eligibility_ms'],
            require_negative_regime=payload.get('require_negative_regime', False),
            signal_reference_fraction_bounds=bounds)
    except (KeyError, TypeError) as exc:
        raise ValueError('Declared early failure policy is incomplete') from exc
    if (release.rule_set_contracts.count(policy.policy_id) != 1
            or canonical_json(payload) != canonical_json(policy.payload())):
        raise ValueError('Early failure policy differs from its exact declared rule')
    return policy
