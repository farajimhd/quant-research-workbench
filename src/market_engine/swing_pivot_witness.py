"""Fresh directional-change evidence, separate from an anchored swing level.

The swing engine uses candle sequence numbers internally. Consumers receive
event times only after the detector translates both clocks explicitly.
"""
from math import isfinite
from .swing_structure import SwingStructure


CONTRACT = 'causal-swing-pivot-witness-1'


def capture(level, extreme, confirmed_at, reason='reversal_confirmed'):
    """Describe an observed reversal without refreshing the level's identity."""
    if reason != 'reversal_confirmed':
        return None
    price, pivot_at, distance = extreme
    if (any(type(v) not in (int, float) or not isfinite(v) or v <= 0
            for v in (price, pivot_at, confirmed_at, distance))
            or not pivot_at < confirmed_at
            or level.get('side') not in ('support', 'resistance')
            or level.get('scale') not in ('local', 'major')
            or level.get('level_id') is None):
        raise ValueError('Invalid confirmed swing pivot witness')
    return dict(contract=CONTRACT, clock='candle_sequence', level_id=level['level_id'],
        side=level['side'], scale=level['scale'], price=price,
        pivot_at=pivot_at, confirmed_at=confirmed_at, reversal_distance=distance)


def event_times(witness, close_times):
    """Map a copied witness through known completed-candle timestamps only."""
    if witness.get('contract') != CONTRACT or witness.get('clock') != 'candle_sequence':
        raise ValueError('Unknown pivot witness clock or contract')
    pivot = close_times.get(witness['pivot_at'])
    confirmed = close_times.get(witness['confirmed_at'])
    if (any(type(v) not in (int, float) or not isfinite(v) or v <= 0
            for v in (pivot, confirmed)) or not pivot < confirmed):
        raise ValueError('Missing or unordered pivot witness event times')
    return dict(witness, clock='event_time', pivot_at=pivot, confirmed_at=confirmed)


class PivotWitnessStructure(SwingStructure):
    """Candle-detector observer; anchored levels and visual output are untouched."""
    def __init__(self, settings):
        super().__init__(settings)
        self.latest_pivots = {}
        self._capturing_pivot = False
        self._pivot_level_id = None

    def _level_updated(self, level):
        super()._level_updated(level)
        key = level['level_id']
        old = self.latest_pivots.get(key)
        if old and (old['side'] != level['side'] or old['scale'] != level['scale']
                or old['confirmed_at'] < level['confirmed_at']):
            self.latest_pivots.pop(key)
        if self._capturing_pivot:
            self._pivot_level_id = key

    def _level_removed(self, key):
        super()._level_removed(key)
        self.latest_pivots.pop(key, None)

    def _found(self, detector, extreme, side, t, reason='reversal_confirmed'):
        self._capturing_pivot = True
        self._pivot_level_id = None
        try:
            super()._found(detector, extreme, side, t, reason)
            # Suppressed interior major pivots have no selected level.
            if self._pivot_level_id is not None:
                level = self.active[self._pivot_level_id]
                witness = capture(level, extreme, t, reason)
                if witness is not None:
                    self.latest_pivots[self._pivot_level_id] = witness
        finally:
            self._capturing_pivot = False
            self._pivot_level_id = None
