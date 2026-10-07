"""Closed first fixed-lot economic declaration; no installed strategy authority.

This first version supports equal allocation only. Later logarithmic weight
variants require their own declaration rather than a parser fallback.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FixedStructuralLotPolicy:
    version: int = 1
    count: int = 3
    allocation: str = 'equal'
    target_selection: str = 'original_plus_next_higher'
    target_price_rule: str = 'inherited_midpoint_nearest_tick'
    target_management: str = 'fixed_for_lot_lifetime'
    stop_management: str = 'inherited_upward_only'
    aggregate_exit: str = 'inherited_flatten_remaining'
    entry_reentry: str = 'inherited'
    profile_id: str = 'fixed-structural-lots-v1'

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 1
                or type(self.count) is not int or not 2 <= self.count <= 32
                or any(type(getattr(self, key)) is not str or getattr(self, key) != value
                       for key, value in _STRINGS.items())):
            raise ValueError('Unsupported fixed structural lot declaration')

    def payload(self):
        self.__post_init__()
        return {'version': self.version, 'count': self.count,
                **{key: getattr(self, key) for key in _STRINGS}}

    @property
    def weights(self):
        self.__post_init__()
        return ((1, self.count),) * self.count


_STRINGS = {
    'allocation': 'equal', 'target_selection': 'original_plus_next_higher',
    'target_price_rule': 'inherited_midpoint_nearest_tick',
    'target_management': 'fixed_for_lot_lifetime',
    'stop_management': 'inherited_upward_only',
    'aggregate_exit': 'inherited_flatten_remaining', 'entry_reentry': 'inherited',
    'profile_id': 'fixed-structural-lots-v1',
}


def parse_fixed_structural_lot_policy(value):
    if type(value) is not dict or set(value) != {'version', 'count', *_STRINGS}:
        raise ValueError('Fixed structural lot declaration keys differ')
    return FixedStructuralLotPolicy(**value)
