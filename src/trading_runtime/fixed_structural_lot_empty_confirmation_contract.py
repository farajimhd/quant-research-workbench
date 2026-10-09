"""Prepared factory selecting commandless confirmation; no source admission."""
from dataclasses import dataclass

from .fixed_structural_lot_owned_snapshot_contract import FixedStructuralLotOwnedSnapshotStrategyContract
from .empty_protection_confirmation_policy import (
    EmptyProtectionConfirmationPolicy, declared_empty_protection_confirmation_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotEmptyConfirmationStrategyContract(FixedStructuralLotOwnedSnapshotStrategyContract):
    empty_confirmation_policy: EmptyProtectionConfirmationPolicy | None = None

    def __post_init__(self):
        FixedStructuralLotOwnedSnapshotStrategyContract.__post_init__(self)
        if type(self.empty_confirmation_policy) is not EmptyProtectionConfirmationPolicy:
            raise ValueError('Explicit typed empty confirmation policy required')
        declared_empty_protection_confirmation_policy(self.release, self.empty_confirmation_policy.payload())
