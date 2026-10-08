"""Pure columnar completed-bar windows; no source or exit admission authority."""
from dataclasses import dataclass

import numpy as np
import polars as pl


_IDENTITY = ('build_id', 'session_date', 'ticker', 'attempt_id', 'feature_attempt_id',
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


def qualify_completed_sequences_after_acquisition(windows, decisions, mandatory_held,
        acquisition_day_us, *, sequence_policy, resolution_ms, decision_interval_ms,
        freshness_ms, max_decisions):
    """Backward-align pure evidence; intersect actual held ownership only.

    Caller still owns source certification, cash, quote-confirmed execution and
    per-lot protection. This result is an immutable predicate, never an order.
    """
    from src.backend.backtest_market_data import FIXED_RESOLUTIONS_MS
    if type(sequence_policy) is not CompletedBarSequencePolicy:
        raise ValueError('Exact supporting sequence policy required')
    sequence_policy.__post_init__()
    if (type(resolution_ms) is not int or resolution_ms not in FIXED_RESOLUTIONS_MS
            or type(decision_interval_ms) is not int or decision_interval_ms not in FIXED_RESOLUTIONS_MS
            or type(freshness_ms) is not int or freshness_ms < 0
            or type(max_decisions) is not int or not 1 <= max_decisions <= 2**32 - 1):
        raise ValueError('Explicit native resolution, decision clock, freshness and bounds required')
    if (type(windows) is not pl.DataFrame or type(decisions) is not pl.DataFrame
            or windows.height > sequence_policy.max_rows or decisions.height > max_decisions):
        raise ValueError('Bounded completed windows and decisions required')
    keys = list(_IDENTITY[:-1])
    for frame in (windows, decisions):
        if any(frame.schema.get(key) != pl.String or frame[key].null_count() or
               frame[key].str.len_chars().min() == 0 for key in keys):
            raise ValueError('Exact complete source identities required')
    expected = dict(resolution_ms=pl.UInt32, bucket_index=pl.UInt32,
                    available_day_boundary_ms=pl.Int64, sequence_eligible=pl.Boolean,
                    sequence_start_day_ms=pl.Int64)
    if (any(windows.schema.get(key) != kind for key, kind in expected.items())
            or decisions.schema.get('decision_day_ms') != pl.Int64):
        raise ValueError('Exact sequence and decision clocks required')
    if windows.select([*_IDENTITY, 'bucket_index']).n_unique() != windows.height:
        raise ValueError('Duplicate supporting source window')
    if decisions.select([*keys, 'decision_day_ms']).n_unique() != decisions.height:
        raise ValueError('Duplicate decision identity')
    if (windows['resolution_ms'].null_count() or windows['bucket_index'].null_count()
            or windows['available_day_boundary_ms'].null_count() or windows['sequence_eligible'].null_count()
            or decisions['decision_day_ms'].null_count()):
        raise ValueError('Missing sequence or decision clock')
    boundary = pl.col('available_day_boundary_ms')
    resolution = pl.col('resolution_ms').cast(pl.Int64)
    start = pl.col('sequence_start_day_ms')
    if windows.filter((resolution <= 0) | (boundary <= 0) |
            (boundary != (pl.col('bucket_index').cast(pl.Int64) + 1) * resolution) |
            (pl.col('sequence_eligible') & (start.is_null() | (start <= 0) | (start > boundary) |
             (boundary - start != (sequence_policy.minimum_bars - 1) * resolution)))).height:
        raise ValueError('Supporting window geometry differs from declaration')
    clock = pl.col('decision_day_ms')
    if decisions.filter((clock < 0) | (clock > 86400000) |
                        (clock % decision_interval_ms != 0)).height:
        raise ValueError('Decision clock differs from declaration')
    if (type(mandatory_held) is not np.ndarray or mandatory_held.dtype != np.dtype(bool)
            or mandatory_held.shape != (decisions.height,)
            or type(acquisition_day_us) is not np.ndarray or acquisition_day_us.dtype != np.dtype(np.int64)
            or acquisition_day_us.shape != (decisions.height,)):
        raise ValueError('Exact actual held and acquisition clock arrays required')
    held = np.frombuffer(mandatory_held.tobytes(), dtype=bool)
    acquired = np.frombuffer(acquisition_day_us.tobytes(), dtype=np.int64)
    now = decisions['decision_day_ms'].to_numpy() * 1000
    if np.any(held & ((acquired < 0) | (acquired > now))):
        raise ValueError('Actual held acquisition is missing or later than decision')
    left = decisions.select([*keys, 'decision_day_ms']).with_row_index('_input_row')
    left = left.with_columns(pl.Series('_held', held), pl.Series('_acquired_us', acquired))
    right = windows.filter(pl.col('resolution_ms') == resolution_ms).select(
        *keys, boundary.alias('_completion_ms'), 'sequence_start_day_ms', 'sequence_eligible')
    aligned = left.sort([*keys, 'decision_day_ms']).join_asof(
        right.sort([*keys, '_completion_ms']), left_on='decision_day_ms', right_on='_completion_ms',
        by=keys, strategy='backward', check_sortedness=False)
    predicate = (pl.col('_held') & pl.col('sequence_eligible') &
                 (pl.col('sequence_start_day_ms') * 1000 > pl.col('_acquired_us')) &
                 (pl.col('decision_day_ms') - pl.col('_completion_ms') <= freshness_ms)).fill_null(False)
    result = aligned.with_columns(predicate.alias('_result')).sort('_input_row')['_result'].to_numpy()
    return np.frombuffer(result.tobytes(), dtype=bool)
