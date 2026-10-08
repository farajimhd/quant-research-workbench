"""Columnar multi-resolution research channels on one declared decision clock."""
import polars as pl

from src.backend.backtest_market_data import FIXED_RESOLUTIONS_MS
from research.causal_strategy_features.v3.native_bars import IDENTITY
from research.causal_strategy_features.v4.decisions import FEATURE_COLUMNS, decision_channels

DECISION_IDENTITY = tuple(c for c in IDENTITY if c != 'resolution_ms')
CHANNEL_COLUMNS = tuple(c for c in FEATURE_COLUMNS if c not in IDENTITY) + (
    'feature_age_ms', 'feature_row_available',
)
MAX_EXPANDED_DECISIONS = 2_000_000


def multi_resolution_decisions(features, decisions, *, decision_interval_ms,
                               freshness_by_resolution):
    """Return one row/decision with a typed channel struct for each timeframe.

    Input decisions carry exact build/day/ticker/bars-attempt identity and local
    midnight milliseconds. Parameters are declarations, not inferred defaults.
    Bounded cross joins and grouped expressions operate on columns; this function
    owns no cash, fills, protection, certification, or strategy state.
    """
    if (type(decision_interval_ms) is not int or
            decision_interval_ms not in FIXED_RESOLUTIONS_MS):
        raise ValueError('Declared native decision interval required')
    if (type(freshness_by_resolution) is not tuple or not freshness_by_resolution or
            any(type(p) is not tuple or len(p) != 2 for p in freshness_by_resolution)):
        raise ValueError('Explicit ordered resolution/freshness pairs required')
    resolutions = tuple(p[0] for p in freshness_by_resolution)
    if (any(type(r) is not int or r not in FIXED_RESOLUTIONS_MS for r in resolutions) or
            resolutions != tuple(sorted(set(resolutions))) or
            any(type(age) is not int or age < 0 for _, age in freshness_by_resolution)):
        raise ValueError('Unique ordered native resolutions and nonnegative freshness required')
    left = decisions.select(*DECISION_IDENTITY, 'decision_day_ms')
    if any(left[c].null_count() for c in left.columns) or left.n_unique() != left.height:
        raise ValueError('Missing or duplicate decision identity/clock')
    if left.height * len(resolutions) > MAX_EXPANDED_DECISIONS:
        raise ValueError('Declared multi-resolution decision packet exceeds bound')
    # Validate the decision clock even for an empty source packet.
    if left.filter((pl.col('decision_day_ms') < 0) |
                   (pl.col('decision_day_ms') > 86400000) |
                   (pl.col('decision_day_ms') % decision_interval_ms != 0)).height:
        raise ValueError('Decision clock differs from declaration')
    if features.filter(~pl.col('resolution_ms').is_in(resolutions)).height:
        raise ValueError('Feature packet contains undeclared source resolutions')
    scope = pl.DataFrame({
        'resolution_ms': pl.Series(resolutions, dtype=features.schema['resolution_ms']),
        '_freshness_ms': pl.Series([p[1] for p in freshness_by_resolution], dtype=pl.Int64),
    })
    expanded = left.with_row_index('_decision_order').join(scope, how='cross')
    aligned = decision_channels(features, expanded, decision_interval_ms=decision_interval_ms,
                                max_age_ms=max(age for _, age in freshness_by_resolution))
    aligned = aligned.join(expanded.select(*IDENTITY, 'decision_day_ms', '_freshness_ms',
                                          '_decision_order'),
                           on=[*IDENTITY, 'decision_day_ms'], how='left', validate='1:1')
    fresh = (pl.col('feature_row_available') &
             (pl.col('feature_age_ms') <= pl.col('_freshness_ms'))).fill_null(False)
    aligned = aligned.with_columns(
        fresh.alias('feature_row_available'),
        *[pl.when(fresh).then(pl.col(c)).otherwise(None).alias(c)
          for c in CHANNEL_COLUMNS if c != 'feature_row_available'],
    )
    return aligned.group_by('_decision_order', *DECISION_IDENTITY, 'decision_day_ms').agg(
        *[pl.struct(CHANNEL_COLUMNS).filter(pl.col('resolution_ms') == r).first()
          .alias(f'channels_{r}ms') for r in resolutions],
    ).sort('_decision_order').drop('_decision_order')
