"""Exact typed lot factory with a separate explicitly selected reuse policy."""
from dataclasses import dataclass

from .fixed_structural_lot_contract import FixedStructuralLotStrategyContract
from .packet_validation_reuse_policy import (
    INPUT, RULE, PacketValidationReusePolicy, declared_packet_validation_reuse_policy,
)
from .fixed_lot_management_reuse_policy import FixedLotManagementReusePolicy, declared_management_reuse_policy


@dataclass(frozen=True, slots=True)
class FixedStructuralLotReuseStrategyContract(FixedStructuralLotStrategyContract):
    validation_reuse_policy: PacketValidationReusePolicy | None = None
    management_reuse_policy: FixedLotManagementReusePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotStrategyContract.__post_init__(self)
        if type(self.validation_reuse_policy) is not PacketValidationReusePolicy:
            raise ValueError('Explicit typed packet reuse policy required')
        declared_packet_validation_reuse_policy(self.release, self.validation_reuse_policy.payload())
        if self.management_reuse_policy is not None and type(self.management_reuse_policy) is not FixedLotManagementReusePolicy:
            raise ValueError('Exact typed management reuse policy required')
        declared_management_reuse_policy(self.release, None if self.management_reuse_policy is None else self.management_reuse_policy.payload())


def require_declared_fixed_structural_lot_contract(contract, release):
    """Select the exact factory shape through semantic declarations only."""
    selected = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    wanted = FixedStructuralLotReuseStrategyContract if selected else FixedStructuralLotStrategyContract
    from .projected_configuration_reuse_policy import INPUT as PROJECTION_INPUT, RULE as PROJECTION_RULE
    if PROJECTION_INPUT in release.input_contracts or PROJECTION_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_projection_reuse_contract import FixedStructuralLotProjectionReuseStrategyContract
        wanted = FixedStructuralLotProjectionReuseStrategyContract
    from .owned_scalar_snapshot_policy import INPUT as OWNED_INPUT, RULE as OWNED_RULE
    if OWNED_INPUT in release.input_contracts or OWNED_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_owned_snapshot_contract import FixedStructuralLotOwnedSnapshotStrategyContract
        wanted = FixedStructuralLotOwnedSnapshotStrategyContract
    from .empty_protection_confirmation_policy import INPUT as EMPTY_INPUT, RULE as EMPTY_RULE
    if EMPTY_INPUT in release.input_contracts or EMPTY_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_empty_confirmation_contract import FixedStructuralLotEmptyConfirmationStrategyContract
        wanted = FixedStructuralLotEmptyConfirmationStrategyContract
    from .complete_market_window_policy import INPUT as WINDOW_INPUT, RULE as WINDOW_RULE
    if WINDOW_INPUT in release.input_contracts or WINDOW_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
        wanted = FixedStructuralLotCompleteMarketStrategyContract
    from .selected_exit_publication_policy import INPUT as EXIT_INPUT, RULE as EXIT_RULE
    if EXIT_INPUT in release.input_contracts or EXIT_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_selected_exit_contract import FixedStructuralLotSelectedExitStrategyContract
        wanted = FixedStructuralLotSelectedExitStrategyContract
    if type(contract) is not wanted or contract.release != release:
        raise ValueError('Fixed-lot factory differs from exact declared release type')
    contract.__post_init__()
    return contract
