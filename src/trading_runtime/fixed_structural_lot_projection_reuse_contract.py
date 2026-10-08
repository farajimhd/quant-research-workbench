"""Prepared-only typed factory for separately declared pure projection reuse."""
from dataclasses import dataclass

from .fixed_structural_lot_reuse_contract import FixedStructuralLotReuseStrategyContract
from .projected_configuration_reuse_policy import (
    ProjectedConfigurationReusePolicy, declared_projected_configuration_reuse_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotProjectionReuseStrategyContract(FixedStructuralLotReuseStrategyContract):
    projection_reuse_policy: ProjectedConfigurationReusePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotReuseStrategyContract.__post_init__(self)
        if type(self.projection_reuse_policy) is not ProjectedConfigurationReusePolicy:
            raise ValueError('Explicit typed projected-node reuse policy required')
        declared_projected_configuration_reuse_policy(self.release, self.projection_reuse_policy.payload())
