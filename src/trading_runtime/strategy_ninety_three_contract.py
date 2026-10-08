"""Prepared three-lot factory; not registered or installed by this module."""
from .fixed_structural_lot_reuse_contract import FixedStructuralLotReuseStrategyContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .journal_contract import canonical_json
from .strategy_forty_two_release import INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY
from .strategy_ninety_three_release import release_contract, VALIDATION_REUSE_POLICY


def strategy_ninety_three_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    return FixedStructuralLotReuseStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies), FixedStructuralLotPolicy(),
        validation_reuse_policy=VALIDATION_REUSE_POLICY,
    )
