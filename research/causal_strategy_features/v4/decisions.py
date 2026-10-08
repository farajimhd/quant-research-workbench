"""Bounded columnar alignment of completed research channels to decision clocks."""
import polars as pl

from src.backend.backtest_market_data import FIXED_RESOLUTIONS_MS
from research.causal_strategy_features.v3.native_bars import COLUMNS, IDENTITY

FEATURE_COLUMNS = (*COLUMNS, 'available_day_boundary_ms', 'candle_available',
    'open_return', 'high_return', 'low_return', 'close_return',
    'relative_execution_volume', 'body_return', 'upper_wick_return', 'lower_wick_return',
    'prior_return_volatility', 'volatility_baseline_available', 'relative_trade_count',
    'bar_duration_seconds', 'source_day_fraction',
    'open_volatility_units', 'high_volatility_units', 'low_volatility_units',
    'close_volatility_units', 'body_volatility_units', 'upper_wick_volatility_units',
    'lower_wick_volatility_units')


def decision_channels(features, decisions, *, decision_interval_ms, max_age_ms):
    """No new bars or state transitions; callers own certification and batch bounds.

    Both clocks are milliseconds since local source midnight. Decisions specify
    exact build/day/ticker/attempt/resolution dependencies. Future labels and
    undeclared columns are projected away before the backwards join.
    """
    if type(decision_interval_ms) is not int or decision_interval_ms not in FIXED_RESOLUTIONS_MS:
        raise ValueError('Declared native decision interval required')
    if type(max_age_ms) is not int or max_age_ms < 0:
        raise ValueError('Explicit nonnegative feature freshness required')
    left = decisions.select(*IDENTITY, 'decision_day_ms')
    right = features.select(FEATURE_COLUMNS)
    if any(left[c].null_count() for c in left.columns):
        raise ValueError('Decision identity or clock is missing')
    if any(right[c].null_count() for c in (*IDENTITY, 'bucket_index', 'available_day_boundary_ms')):
        raise ValueError('Feature identity or availability is missing')
    if left.n_unique() != left.height or right.select(*IDENTITY, 'bucket_index').n_unique() != right.height:
        raise ValueError('Duplicate decision or completed feature identity')
    if left.filter((pl.col('decision_day_ms') < 0) | (pl.col('decision_day_ms') > 86400000) |
                   (pl.col('decision_day_ms') % decision_interval_ms != 0) |
                   ~pl.col('resolution_ms').is_in(FIXED_RESOLUTIONS_MS)).height:
        raise ValueError('Decision clock or source resolution differs from declaration')
    if right.filter(~pl.col('resolution_ms').is_in(FIXED_RESOLUTIONS_MS) |
                    (pl.col('available_day_boundary_ms') !=
                     (pl.col('bucket_index').cast(pl.Int64) + 1) * pl.col('resolution_ms')) |
                    (pl.col('available_day_boundary_ms') > 86400000)).height:
        raise ValueError('Completed feature clock differs from source bucket')
    joined = left.with_row_index('_decision_order').sort(*IDENTITY, 'decision_day_ms').join_asof(
        right.sort(*IDENTITY, 'available_day_boundary_ms'), by=list(IDENTITY),
        left_on='decision_day_ms', right_on='available_day_boundary_ms',
        strategy='backward', tolerance=max_age_ms, check_sortedness=False,
    )
    return joined.with_columns(
        (pl.col('decision_day_ms') - pl.col('available_day_boundary_ms')).alias('feature_age_ms'),
        pl.col('available_day_boundary_ms').is_not_null().alias('feature_row_available'),
    ).sort('_decision_order').drop('_decision_order')
