"""Closed declared native interpretation; no numbered-strategy dispatch."""
from dataclasses import dataclass, field
from math import isfinite,floor,ceil
from .profit_armed_structural_rejection import StructuralRejectionPolicy

RULE = 'profit-armed-structural-rejection-liquidity@1'
SOURCE_INPUT = 'profit-armed-structural-rejection-source@1'


@dataclass(frozen=True, slots=True)
class NativeStructuralRejectionDeclaration:
    policy: StructuralRejectionPolicy = field(default_factory=StructuralRejectionPolicy)
    source_input: str = SOURCE_INPUT
    resistance_price_basis: str = 'midpoint_floor'
    resistance_selection: str = 'lowest_actually_crossed_then_level_id'
    midpoint_arithmetic: str = 'native_float64_midpoint_floor'
    geometry_bound_conversion: str = 'outward_float64_canonical_envelope'

    def __post_init__(self):
        if (type(self.policy) is not StructuralRejectionPolicy
                or type(self.source_input) is not str or self.source_input != SOURCE_INPUT
                or type(self.resistance_price_basis) is not str
                or self.resistance_price_basis != 'midpoint_floor'
                or type(self.resistance_selection) is not str
                or self.resistance_selection != 'lowest_actually_crossed_then_level_id'
                or self.midpoint_arithmetic != 'native_float64_midpoint_floor'
                or type(self.midpoint_arithmetic) is not str
                or self.geometry_bound_conversion != 'outward_float64_canonical_envelope'
                or type(self.geometry_bound_conversion) is not str):
            raise ValueError('Exact native structural rejection declaration required')
        self.policy.__post_init__()

    def payload(self):
        return dict(policy=self.policy.payload(),source_input=self.source_input,
            resistance_price_basis=self.resistance_price_basis,
            resistance_selection=self.resistance_selection,midpoint_arithmetic=self.midpoint_arithmetic,
            geometry_bound_conversion=self.geometry_bound_conversion)

    def geometry_ints(self,lower,upper):
        """Declared trigger conversion, preserving the raw geometry elsewhere."""
        self.__post_init__()
        if (type(lower) is not float or type(upper) is not float
                or not isfinite(lower) or not isfinite(upper) or not 0<lower<=upper):
            raise ValueError('Certified native geometry requires positive finite Float64 edges')
        bounds=(lower*10000,upper*10000)
        midpoint=(lower+upper)/2*10000
        if not all(isfinite(value) for value in (*bounds,midpoint)):
            raise ValueError('Native geometry conversion overflowed')
        values=(floor(bounds[0]),ceil(bounds[1]),floor(midpoint))
        if (any(not 0<value<2**64 for value in values)
                or not values[0]<=values[2]<=values[1]):
            raise ValueError('Native geometry exceeds positive UInt64 canonical envelope')
        return values


def native_structural_rejection_declaration(contract):
    declaration = getattr(contract,'profit_armed_structural_rejection_policy',None)
    release = getattr(contract,'release',None)
    if declaration is None:
        if release is not None and (RULE in release.rule_set_contracts or SOURCE_INPUT in release.input_contracts):
            raise ValueError('Declared native rejection lacks its typed policy')
        return None
    if type(declaration) is not NativeStructuralRejectionDeclaration:
        raise ValueError('Native rejection requires its exact typed declaration')
    declaration.__post_init__()
    if release is None:
        raise ValueError('Native rejection lacks a declared release')
    if (release.rule_set_contracts.count(RULE) != 1
            or release.input_contracts.count(SOURCE_INPUT) != 1):
        raise ValueError('Native rejection lacks declared rule/source conjunction')
    return declaration
