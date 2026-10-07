"""Typed declared fixed-lot capabilities; no numeric trading branches."""
from dataclasses import dataclass, field

from .fixed_structural_lot_policy import FixedStructuralLotPolicy
from .numbered_fixed_strategy import DeclaredFixedStrategyContract


@dataclass(frozen=True, slots=True)
class FixedStructuralLotStrategyContract(DeclaredFixedStrategyContract):
    selected_policy: FixedStructuralLotPolicy = field(default_factory=FixedStructuralLotPolicy)

    def __post_init__(self):
        DeclaredFixedStrategyContract.__post_init__(self)
        if type(self.selected_policy) is not FixedStructuralLotPolicy:
            raise ValueError('Exact typed fixed-lot contract policy required')
        self.selected_policy.__post_init__()
        if (sum(self.release.input_contracts.count(source) for source in
                    ('fixed-structural-lot-source@1', 'fixed-structural-lot-source@2')) != 1
                or self.release.rule_set_contracts.count('fixed-structural-lot-entry@1') != 1):
            raise ValueError('Fixed-lot contract lacks exact selected source and entry declarations')

    @property
    def fixed_structural_lot_policy(self):
        return self.selected_policy
