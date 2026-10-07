"""Declared reentry adapter; shared42 economics and exits remain unchanged."""
from .journal_contract import canonical_json
from .numbered_fixed_strategy import DeclaredFixedStrategyContract
from .strategy_seventy_eight_release import release_contract, INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY, REENTRY_POLICY


def strategy_seventy_eight_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY,
                'prior_position_high_reentry_policy': REENTRY_POLICY}
    return DeclaredFixedStrategyContract(release.number,release.executor_strategy_id,
        release.evaluation_interval,release,canonical_json(policies))
