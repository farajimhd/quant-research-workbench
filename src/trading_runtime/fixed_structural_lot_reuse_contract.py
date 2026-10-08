"""Exact typed lot factory with a separate explicitly selected reuse policy."""
from dataclasses import dataclass

from .fixed_structural_lot_contract import FixedStructuralLotStrategyContract
from .packet_validation_reuse_policy import (
    PacketValidationReusePolicy, declared_packet_validation_reuse_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotReuseStrategyContract(FixedStructuralLotStrategyContract):
    validation_reuse_policy: PacketValidationReusePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotStrategyContract.__post_init__(self)
        if type(self.validation_reuse_policy) is not PacketValidationReusePolicy:
            raise ValueError('Explicit typed packet reuse policy required')
        declared_packet_validation_reuse_policy(self.release, self.validation_reuse_policy.payload())
