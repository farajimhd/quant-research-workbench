"""Prepared selected-exit factory; declaration does not grant execution."""
from dataclasses import dataclass

from .portfolio_acquisition_contract import SessionAcquisitionQuotaPolicy
from .fixed_lot_management_cadence_policy import FixedLotManagementCadencePolicy
from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
from .selected_exit_publication_policy import (
    SelectedExitPublicationPolicy, declared_selected_exit_publication_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotSelectedExitStrategyContract(FixedStructuralLotCompleteMarketStrategyContract):
    selected_exit_publication_policy: SelectedExitPublicationPolicy | None = None
    session_acquisition_quota: SessionAcquisitionQuotaPolicy | None = None

    management_cadence_policy: FixedLotManagementCadencePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotCompleteMarketStrategyContract.__post_init__(self)
        if type(self.selected_exit_publication_policy) is not SelectedExitPublicationPolicy:
            raise ValueError('Explicit typed selected exit publication policy required')
        from .portfolio_acquisition_contract import require_declared_acquisition_policy
        require_declared_acquisition_policy(self.release,self.session_acquisition_quota)
        from .fixed_lot_management_cadence_policy import require_declared_management_cadence
        require_declared_management_cadence(self.release,self.management_cadence_policy)
        declared_selected_exit_publication_policy(
            self.release, self.selected_exit_publication_policy.payload())
