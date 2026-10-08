"""Pure columnar completed-bar windows; no source or exit admission authority."""
from dataclasses import dataclass

import numpy as np
import polars as pl


_IDENTITY = ('build_id', 'session_date', 'ticker', 'feature_attempt_id',
             'policy_digest', 'resolution_ms')


@dataclass(frozen=True, slots=True)
class CompletedBarSequencePolicy:
    minimum_bars: int
    max_rows: int

    def __post_init__(self):
        if (type(self.minimum_bars) is not int or type(self.max_rows) is not int
                or not 1 <= self.minimum_bars <= self.max_rows <= 2**32 - 1):
            raise ValueError('Explicit positive bounded sequence parameters required')


def completed_bar_sequence_windows(features, bar_eligible, policy):
    """Preserve input row order and return each causal supporting window.

    The caller must certify feature provenance and derive bar_eligible from
    declared rules. This guard cannot establish ownership or authorize orders.
    Supporting completion times must be aligned backward to decision time;
    compare the first completion strictly with the actual acquisition clock.
    """
    if type(policy) is not CompletedBarSequencePolicy:
        raise ValueError('Exact completed-bar sequence policy required')
    policy.__post_init__()
    if type(features) is not pl.DataFrame or features.height > policy.max_rows:
        raise ValueError('Bounded completed feature frame required')
    schema = dict.fromkeys(_IDENTITY[:-1], pl.String)
    schema.update(resolution_ms=pl.UInt32, bucket_index=pl.UInt32,
                  available_day_boundary_ms=pl.Int64, candle_available=pl.Boolean)
    if any(features.schema.get(name) != dtype for name, dtype in schema.items()):
        raise ValueError('Exact source identity and completed clock columns required')
    if (type(bar_eligible) is not np.ndarray or bar_eligible.dtype != np.dtype(bool)
            or bar_eligible.shape != (features.height,)):
        raise ValueError('Exact declared bar predicate mask required')
    data = features.select(list(schema))
    if data.null_count().sum_horizontal().item() != 0:
        raise ValueError('Sequence identity and completed clocks cannot be null')
    if any(data[name].str.len_chars().min() == 0 for name in _IDENTITY[:-1]):
        raise ValueError('Sequence source identity cannot be empty')
    if data.select([*_IDENTITY, 'bucket_index']).n_unique() != data.height:
        raise ValueError('Duplicate completed source bar')
    resolution = pl.col('resolution_ms').cast(pl.Int64)
    boundary = pl.col('available_day_boundary_ms')
    if data.filter((resolution <= 0) | (boundary <= 0) |
                   (boundary != (pl.col('bucket_index').cast(pl.Int64) + 1) * resolution)).height:
        raise ValueError('Completed boundary differs from source resolution grid')
    mask = np.frombuffer(bar_eligible.tobytes(), dtype=bool)
    data = data.with_row_index('_input_row').with_columns(pl.Series('_bar_eligible', mask))
    data = data.sort([*_IDENTITY, 'available_day_boundary_ms'])
    count = policy.minimum_bars
    data = data.with_columns(
        boundary.shift(count - 1).over(list(_IDENTITY)).alias('_first_completion'),
        (pl.col('_bar_eligible') & pl.col('candle_available')).cast(pl.UInt32)
        .rolling_sum(count, min_samples=count).over(list(_IDENTITY)).alias('_support_count'))
    eligible = ((pl.col('_support_count') == count) &
                (boundary - pl.col('_first_completion') == (count - 1) * resolution)).fill_null(False)
    return (data.with_columns(eligible.alias('sequence_eligible')).with_columns(
        pl.when(pl.col('sequence_eligible')).then(pl.col('_first_completion'))
        .otherwise(None).alias('sequence_start_day_ms'))
        .sort('_input_row').select([*_IDENTITY, 'bucket_index', 'available_day_boundary_ms',
                                   'sequence_eligible', 'sequence_start_day_ms']))
