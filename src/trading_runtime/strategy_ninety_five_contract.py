"""Prepared typed factory; no registration or installed source authority."""
from .fixed_structural_lot_owned_snapshot_contract import FixedStructuralLotOwnedSnapshotStrategyContract
from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .journal_contract import canonical_json
from .strategy_forty_two_release import INHERITED_POLICIES, HALF_RISK_LIQUIDITY_POLICY
from .strategy_ninety_five_release import (
    release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY, OWNED_SNAPSHOT_POLICY,
)


def strategy_ninety_five_contract():
    release = release_contract()
    policies = {**INHERITED_POLICIES, 'half_risk_liquidity_policy': HALF_RISK_LIQUIDITY_POLICY}
    return FixedStructuralLotOwnedSnapshotStrategyContract(
        release.number, release.executor_strategy_id, release.evaluation_interval,
        release, canonical_json(policies), FixedStructuralLotPolicy(),
        validation_reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY,
        owned_snapshot_policy=OWNED_SNAPSHOT_POLICY)
