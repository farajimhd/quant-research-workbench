"""Immutable three-equal-lot factory over unchanged Strategy42 capabilities."""
from .fixed_structural_lot_contract import FixedStructuralLotStrategyContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .journal_contract import canonical_json
from .strategy_forty_two_release import INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY
from .strategy_eighty_seven_release import release_contract


def strategy_eighty_seven_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    return FixedStructuralLotStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies), FixedStructuralLotPolicy(),
    )
