"""Versioned strategy interpretation of the experimental point-price book."""
from math import isfinite
from src.trading_runtime.normalized_level_book import DEFAULT_THRESHOLD, CONTRACT

BOOK_VERSION = 'clickhouse-closing-book-1'
STRATEGY_CONTRACT = 'clickhouse-merged-point-pnorm-v1'
MINIMUM_PROMINENCE = 4.0


def is_point_level(row):
    return row.get('book_version') == BOOK_VERSION


def qualifies(row, observed_at=None):
    if not is_point_level(row):
        return False
    try:
        score, price = float(row['prominence']), float(row['price'])
        if row.get('load_contract') in {'merged-point-minmax-v1', 'merged-point-minmax-v2', CONTRACT}:
            score = float(row['p_norm'])
            threshold = float(row.get('minimum_p_norm', DEFAULT_THRESHOLD))
            if not 0 <= score <= 1 or not 0 <= threshold <= 1 or score < threshold:
                return False
        elif score < MINIMUM_PROMINENCE:
            return False
        if not isfinite(score) or not isfinite(price) or price <= 0:
            return False
        if row.get('side') not in (-1, 1):
            return False
        if observed_at is not None:
            now = observed_at.timestamp() * 1000
            if any(not isfinite(float(row[key])) or float(row[key]) > now
                   for key in ('created_at_ms', 'confirmed_at_ms')):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def strategy_snapshot(snapshot, observed_at, minimum_p_norm=DEFAULT_THRESHOLD):
    """Default to point prices; retain bands for opt-in breakout/rejection rules."""
    rows = [dict(row, minimum_p_norm=minimum_p_norm) if row.get('load_contract') else row for row in snapshot['unified_levels']]
    return {'unified_levels': [dict(row, band_lower=row['lower'], band_upper=row['upper'],
        lower=row['price'], upper=row['price'], strategy_level_contract=STRATEGY_CONTRACT)
        for row in rows if qualifies(row, observed_at)]}
