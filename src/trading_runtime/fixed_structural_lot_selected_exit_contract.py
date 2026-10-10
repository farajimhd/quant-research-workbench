"""Prepared selected-exit factory; declaration does not grant execution."""
from dataclasses import dataclass

from .portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy
from .fixed_lot_management_cadence_policy import FixedLotManagementCadencePolicy
from .initial_held_recovery_reuse_policy import InitialHeldRecoveryReusePolicy
from .proposal_decision_inventory_reuse_policy import ProposalDecisionInventoryReusePolicy
from .first_inventory_source_reuse_policy import FirstInventorySourceReusePolicy
from .publication_source_reuse_policy import PublicationSourceReusePolicy
from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
from .selected_exit_publication_policy import (
    SelectedExitPublicationPolicy, declared_selected_exit_publication_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotSelectedExitStrategyContract(FixedStructuralLotCompleteMarketStrategyContract):
    selected_exit_publication_policy: SelectedExitPublicationPolicy | None = None
    session_acquisition_quota: SessionAcquisitionQuotaPolicy | None = None

    management_cadence_policy: FixedLotManagementCadencePolicy | None = None
    initial_held_recovery_reuse_policy: InitialHeldRecoveryReusePolicy | None = None

    proposal_decision_inventory_reuse_policy: ProposalDecisionInventoryReusePolicy | None = None
    first_inventory_source_reuse_policy: FirstInventorySourceReusePolicy | None = None
    publication_source_reuse_policy: PublicationSourceReusePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotCompleteMarketStrategyContract.__post_init__(self)
        if type(self.selected_exit_publication_policy) is not SelectedExitPublicationPolicy:
            raise ValueError('Explicit typed selected exit publication policy required')
        from .portfolio_acquisition_contract import require_declared_acquisition_policy
        require_declared_acquisition_policy(self.release,self.session_acquisition_quota)
        from .fixed_lot_management_cadence_policy import require_declared_management_cadence
        require_declared_management_cadence(self.release,self.management_cadence_policy)
        from .initial_held_recovery_reuse_policy import require_declared_initial_held_reuse
        require_declared_initial_held_reuse(self.release,self.initial_held_recovery_reuse_policy)
        from .proposal_decision_inventory_reuse_policy import require_declared_proposal_decision_reuse
        require_declared_proposal_decision_reuse(self.release,self.proposal_decision_inventory_reuse_policy)
        from .first_inventory_source_reuse_policy import require_declared_first_inventory_source_reuse
        require_declared_first_inventory_source_reuse(self.release,self.first_inventory_source_reuse_policy)
        from .publication_source_reuse_policy import require_declared_publication_source_reuse
        require_declared_publication_source_reuse(self.release,self.publication_source_reuse_policy)
        declared_selected_exit_publication_policy(
            self.release, self.selected_exit_publication_policy.payload())
