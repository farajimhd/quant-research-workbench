"""Prepared-only projection comparison; no runtime registration."""
from .fixed_structural_lot_projection_reuse_contract import FixedStructuralLotProjectionReuseStrategyContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .journal_contract import canonical_json
from .strategy_forty_two_release import INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY
from .strategy_ninety_four_release import release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY


def strategy_ninety_four_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    return FixedStructuralLotProjectionReuseStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies), FixedStructuralLotPolicy(),
        validation_reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY)
