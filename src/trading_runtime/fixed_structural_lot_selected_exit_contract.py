"""Prepared selected-exit factory; declaration does not grant execution."""
from dataclasses import dataclass

from .fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
from .selected_exit_publication_policy import (
    SelectedExitPublicationPolicy, declared_selected_exit_publication_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotSelectedExitStrategyContract(FixedStructuralLotCompleteMarketStrategyContract):
    selected_exit_publication_policy: SelectedExitPublicationPolicy | None = None

    def __post_init__(self):
        FixedStructuralLotCompleteMarketStrategyContract.__post_init__(self)
        if type(self.selected_exit_publication_policy) is not SelectedExitPublicationPolicy:
            raise ValueError('Explicit typed selected exit publication policy required')
        declared_selected_exit_publication_policy(
            self.release, self.selected_exit_publication_policy.payload())
