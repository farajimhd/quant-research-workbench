"""Versioned strategy interpretation of the experimental point-price book."""
from math import isfinite
from src.trading_runtime.normalized_level_book import DEFAULT_THRESHOLD, CONTRACT

BOOK_VERSION = 'clickhouse-closing-book-1'
STRATEGY_CONTRACT = 'clickhouse-merged-point-pnorm-v1'
MINIMUM_PROMINENCE = 4.0


def is_point_level(row):
    return row.get('book_version') in (BOOK_VERSION, 'causal-swing-closing-book-1', 'causal-swing-closing-book-2', 'causal-swing-closing-book-3', 'causal-swing-closing-book-4', 'causal-swing-closing-book-5', 'causal-swing-closing-book-6')


def qualifies(row, observed_at=None, *, include_retained=False):
    if row.get('book_version')=='causal-level-book-v7-mle-1':
        try:
            return (row.get('lifecycle')=='active' and (row.get('side') in (-1,1) or (row.get('side')==0 and row.get('role')=='transition'))
                and all(isfinite(float(row[k])) for k in ('lower','price','upper','confirmed_at_ms'))
                and 0 < row['lower'] <= row['price'] <= row['upper']
                and row.get('fit',{}).get('status')=='estimated'
                and (observed_at is None or row['confirmed_at_ms']<=observed_at.timestamp()*1000))
        except (KeyError,TypeError,ValueError):return False
    if not is_point_level(row):
        return False
    try:
        score, price = float(row['prominence']), float(row['price'])
        if row.get('book_version') in ('causal-swing-closing-book-5','causal-swing-closing-book-6'):
            symmetric=row.get('load_contract')=='symmetric-level-evidence-selection-2'
            retained = (include_retained and row.get('retained_qualified_resistance') is True
                        and row.get('side')==-1 and row.get('lifecycle') in ('awaiting_retest','retest_contact'))
            # Broken supports are retained in raw structural evidence, but are
            # never eligible as active protection in the strategy projection.
            if row.get('lifecycle')!='active' and not retained:return False
            if row.get('side')==-1 or symmetric:
                grade=float(row['selection_score'])
                threshold=float(row.get('selection_minimum_score',30)) if symmetric else 30.
                if not isfinite(threshold) or not 0<=threshold<=100 or not isfinite(grade) or not threshold<=grade<=100:return False
        elif row.get('load_contract') in {'merged-point-minmax-v1', 'merged-point-minmax-v2', 'merged-point-minmax-v3', CONTRACT}:
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
    if snapshot.get('book_version')=='causal-level-book-v7-mle-1':
        from src.market_engine.immutable_evidence import FrozenDict
        return dict(snapshot,unified_levels=[row.derived('v7_strategy_band',lambda:dict(row,strategy_level_contract='v7-mle-bands-1'))
            if isinstance(row,FrozenDict) else dict(row,strategy_level_contract='v7-mle-bands-1')
            for row in snapshot['unified_levels'] if qualifies(row,observed_at)])
    rows = [dict(row, minimum_p_norm=minimum_p_norm) if row.get('load_contract') else row for row in snapshot['unified_levels']]
    return {'unified_levels': [dict(row, band_lower=row['lower'], band_upper=row['upper'],
        lower=row['price'], upper=row['price'], strategy_level_contract=STRATEGY_CONTRACT)
        for row in rows if qualifies(row, observed_at, include_retained=True)]}
