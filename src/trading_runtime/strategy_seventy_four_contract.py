"""Exact Strategy74 declaration adapter; economics remain shared Portfolio work."""
from .journal_contract import canonical_json
from .numbered_fixed_strategy import DeclaredFixedStrategyContract
from .strategy_seventy_four_release import (
    release_contract, INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY,
    CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD, PREMARKET_CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD,
)


def strategy_seventy_four_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES,
                'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY,
                'confirmed_original_risk_policy': CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD,
                'premarket_confirmed_original_risk_policy': PREMARKET_CONFIRMED_ORIGINAL_RISK_POLICY_PAYLOAD}
    return DeclaredFixedStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies))
