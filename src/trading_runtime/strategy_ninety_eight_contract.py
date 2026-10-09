"""Prepared exact typed factory; does not register or approve execution."""
from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .journal_contract import canonical_json
from .strategy_forty_two_release import INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY
from .strategy_ninety_eight_release import (
    release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY,
    OWNED_SNAPSHOT_POLICY, EMPTY_CONFIRMATION_POLICY, COMPLETE_MARKET_POLICY,
)


def strategy_ninety_eight_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    return FixedStructuralLotCompleteMarketStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies), FixedStructuralLotPolicy(),
        validation_reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY, empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
        complete_market_policy=COMPLETE_MARKET_POLICY)
