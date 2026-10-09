"""Prepared typed complete-window factory; no installed source admission."""
from dataclasses import dataclass

from .fixed_structural_lot_empty_confirmation_contract import FixedStructuralLotEmptyConfirmationStrategyContract
from .complete_market_window_policy import CompleteMarketWindowPolicy, declared_complete_market_window_policy


@dataclass(frozen=True, slots=True)
class FixedStructuralLotCompleteMarketStrategyContract(FixedStructuralLotEmptyConfirmationStrategyContract):
    complete_market_policy: CompleteMarketWindowPolicy | None = None

    def __post_init__(self):
        FixedStructuralLotEmptyConfirmationStrategyContract.__post_init__(self)
        if type(self.complete_market_policy) is not CompleteMarketWindowPolicy:
            raise ValueError('Explicit typed complete market window policy required')
        declared_complete_market_window_policy(self.release, self.complete_market_policy.payload())
