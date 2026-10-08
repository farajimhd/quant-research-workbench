"""Exact typed lot factory with a separate explicitly selected reuse policy."""
from dataclasses import dataclass

from .fixed_structural_lot_contract import FixedStructuralLotStrategyContract
from .packet_validation_reuse_policy import (
    INPUT, RULE, PacketValidationReusePolicy, declared_packet_validation_reuse_policy,
)


@dataclass(frozen=True, slots=True)
class FixedStructuralLotReuseStrategyContract(FixedStructuralLotStrategyContract):
    validation_reuse_policy: PacketValidationReusePolicy | None = None

    def __post_init__(self):
        FixedStructuralLotStrategyContract.__post_init__(self)
        if type(self.validation_reuse_policy) is not PacketValidationReusePolicy:
            raise ValueError('Explicit typed packet reuse policy required')
        declared_packet_validation_reuse_policy(self.release, self.validation_reuse_policy.payload())


def require_declared_fixed_structural_lot_contract(contract, release):
    """Select the exact factory shape through semantic declarations only."""
    selected = INPUT in release.input_contracts or RULE in release.rule_set_contracts
    wanted = FixedStructuralLotReuseStrategyContract if selected else FixedStructuralLotStrategyContract
    from .projected_configuration_reuse_policy import INPUT as PROJECTION_INPUT, RULE as PROJECTION_RULE
    if PROJECTION_INPUT in release.input_contracts or PROJECTION_RULE in release.rule_set_contracts:
        from .fixed_structural_lot_projection_reuse_contract import FixedStructuralLotProjectionReuseStrategyContract
        wanted = FixedStructuralLotProjectionReuseStrategyContract
    if type(contract) is not wanted or contract.release != release:
        raise ValueError('Fixed-lot factory differs from exact declared release type')
    contract.__post_init__()
    return contract
