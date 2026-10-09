"""Prepared exact factory for independently declared scalar ownership reuse."""
from dataclasses import dataclass

from .fixed_structural_lot_projection_reuse_contract import FixedStructuralLotProjectionReuseStrategyContract
from .owned_scalar_snapshot_policy import OwnedScalarSnapshotPolicy, declared_owned_scalar_snapshot_policy


@dataclass(frozen=True, slots=True)
class FixedStructuralLotOwnedSnapshotStrategyContract(FixedStructuralLotProjectionReuseStrategyContract):
    owned_snapshot_policy: OwnedScalarSnapshotPolicy | None = None

    def __post_init__(self):
        FixedStructuralLotProjectionReuseStrategyContract.__post_init__(self)
        if type(self.owned_snapshot_policy) is not OwnedScalarSnapshotPolicy:
            raise ValueError('Explicit typed owned snapshot policy required')
        declared_owned_scalar_snapshot_policy(self.release, self.owned_snapshot_policy.payload())
        if self.owned_snapshot_policy.max_rows != self.validation_reuse_policy.max_rows:
            raise ValueError('Owned and validation row bounds differ')
