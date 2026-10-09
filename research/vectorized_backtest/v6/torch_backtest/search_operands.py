"""Campaign-bound searchable operands; source banks are never filtered."""
from datetime import datetime, time
from zoneinfo import ZoneInfo
from .feature_bank import CATALOG

REGIME_FLAGS = frozenset(('premarket', 'regular', 'after_hours'))


def searchable_features(training, session='all'):
    if session not in ('all', 'premarket'):
        raise ValueError('Unknown operand session')
    if session == 'premarket':
        if not training:
            raise ValueError('Premarket operand restriction requires training sessions')
        for item in training:
            start = datetime.fromisoformat(item['start'])
            end = datetime.fromisoformat(item['end'])
            if start.utcoffset() is None or end.utcoffset() is None:
                raise ValueError('Premarket boundaries require timezone-aware timestamps')
            start, end = (v.astimezone(ZoneInfo('America/New_York')) for v in (start, end))
            if (start.date() != end.date() or not start < end
                    or start.time() < time(4) or end.time() > time(9, 30)):
                raise ValueError('Premarket operands require a 04:00..09:30 NY training boundary')
    return tuple(i for i, feature in enumerate(CATALOG)
                 if session == 'all' or feature.name not in REGIME_FLAGS)
