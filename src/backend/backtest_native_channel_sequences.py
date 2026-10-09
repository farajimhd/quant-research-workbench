"""Certified installed completed-channel evidence; never order authority."""
from dataclasses import dataclass
from math import isfinite

import numpy as np
import polars as pl

from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
from src.backend.backtest_native_channel_store import load_declared_native_channels
from src.market_engine.native_causal_channel_contract import (
    NativeChannelPolicy, NativeChannelRequest, RELATIVE_FIELDS,
)
from src.trading_runtime.native_completed_bar_sequences import (
    CompletedBarSequencePolicy, completed_bar_sequence_windows,
    qualify_completed_sequences_after_acquisition,
)


@dataclass(frozen=True, slots=True)
class NativeChannelSequencePolicy:
    inputs: NativeChannelPolicy
    sequence: CompletedBarSequencePolicy
    resolution_ms: int
    channel: str
    comparison: str
    threshold: float
    max_decisions: int
    reference_mode: str

    def __post_init__(self):
        if (type(self.inputs) is not NativeChannelPolicy or
                type(self.sequence) is not CompletedBarSequencePolicy):
            raise ValueError('Exact declared input and sequence policies required')
        self.inputs.__post_init__()
        self.sequence.__post_init__()
        if (type(self.resolution_ms) is not int or
                self.resolution_ms not in self.inputs.resolutions_ms or
                type(self.channel) is not str or self.channel not in RELATIVE_FIELDS or
                type(self.comparison) is not str or self.comparison not in ('lt', 'le', 'gt', 'ge') or
                type(self.threshold) is not float or not isfinite(self.threshold) or
                type(self.reference_mode) is not str or
                self.reference_mode not in ('none', 'below-first-post-acquisition-close') or
                type(self.max_decisions) is not int or not 1 <= self.max_decisions <= 2**32 - 1):
            raise ValueError('Complete bounded finite channel sequence declaration required')

    def payload(self):
        self.__post_init__()
        return dict(rule='native-post-acquisition-channel-sequence@2',
            input_policy=self.inputs.payload(), input_policy_digest=self.inputs.digest,
            minimum_bars=self.sequence.minimum_bars, max_rows=self.sequence.max_rows,
            resolution_ms=self.resolution_ms, channel=self.channel,
            comparison=self.comparison, threshold=self.threshold,
            max_decisions=self.max_decisions, reference_mode=self.reference_mode)


def _reference_confirmation(features, decisions, acquired):
    """Columnar first available post-fill close, visible only on completion.

    Inputs are already source/schema validated by the installed adapter. The
    first close is an observed reference, not a broker acquisition price.
    """
    keys = [*DECISION_IDENTITY, 'feature_attempt_id', 'policy_digest']
    left = decisions.select(*keys, 'decision_day_ms').with_row_index('_input_row')
    # A completed boundary is integral milliseconds. floor(fill_us/1000)+1
    # excludes a completion equal to the fill, including submillisecond fills.
    left = left.with_columns(pl.Series('_after_fill_ms', acquired // 1000 + 1))
    source = features.select(*keys, 'available_day_boundary_ms', 'candle_available', 'close_int')
    first = source.filter(pl.col('candle_available') & (pl.col('close_int') > 0)).select(
        *keys, pl.col('available_day_boundary_ms').alias('_first_completion'),
        pl.col('close_int').alias('_first_close'))
    left = left.sort([*keys, '_after_fill_ms']).join_asof(
        first.sort([*keys, '_first_completion']), left_on='_after_fill_ms',
        right_on='_first_completion', by=keys, strategy='forward', check_sortedness=False)
    latest = source.select(*keys, pl.col('available_day_boundary_ms').alias('_latest_completion'),
        pl.col('close_int').alias('_latest_close'), 'candle_available')
    left = left.sort([*keys, 'decision_day_ms']).join_asof(
        latest.sort([*keys, '_latest_completion']), left_on='decision_day_ms',
        right_on='_latest_completion', by=keys, strategy='backward', check_sortedness=False)
    predicate = ((pl.col('_first_completion') <= pl.col('decision_day_ms')) &
        pl.col('candle_available') & (pl.col('_latest_close') > 0) &
        (pl.col('_latest_close') < pl.col('_first_close'))).fill_null(False)
    return left.with_columns(predicate.alias('_reference')).sort('_input_row')['_reference'].to_numpy()


def qualify_installed_native_channel_sequences(client, request, feature_attempt_id,
        decisions, mandatory_held, acquisition_day_us, *, policy,
        producer_source_hash, authority):
    """Load once per bounded preparation, preserving actual owner clocks.

    Caller must supply Portfolio/OMS held state and actual acquisition clocks.
    This function certifies the feature source, not ownership or exit execution.
    Strict versus inclusive comparisons are explicit; no default thresholds.
    """
    if (type(request) is not NativeChannelRequest or
            type(policy) is not NativeChannelSequencePolicy or
            type(decisions) is not pl.DataFrame):
        raise ValueError('Typed installed sequence inputs required')
    request.__post_init__()
    policy.__post_init__()
    if request.policy != policy.inputs:
        raise ValueError('Sequence input policy differs from installed request')
    schema = dict.fromkeys(DECISION_IDENTITY, pl.String)
    schema['decision_day_ms'] = pl.Int64
    if (decisions.height > policy.max_decisions or
            any(decisions.schema.get(k) != v for k, v in schema.items())):
        raise ValueError('Bounded exact sequence decision schema required')
    left = decisions.select(list(schema))
    clock = pl.col('decision_day_ms')
    if (any(left[c].null_count() for c in left.columns) or left.n_unique() != left.height or
            left.filter((clock < 0) | (clock > request.through_day_boundary_ms) |
                (clock % policy.inputs.decision_interval_ms != 0)).height):
        raise ValueError('Sequence decision identity or declared clock differs')
    if (type(mandatory_held) is not np.ndarray or mandatory_held.dtype != np.dtype(bool) or
            mandatory_held.shape != (left.height,) or
            type(acquisition_day_us) is not np.ndarray or acquisition_day_us.dtype != np.dtype(np.int64) or
            acquisition_day_us.shape != (left.height,)):
        raise ValueError('Exact held and actual acquisition arrays required')
    held = np.frombuffer(mandatory_held.tobytes(), dtype=bool)
    acquired = np.frombuffer(acquisition_day_us.tobytes(), dtype=np.int64)
    if np.any(held & ((acquired < 0) | (acquired > left['decision_day_ms'].to_numpy() * 1000))):
        raise ValueError('Held acquisition is missing or later than decision')
    packet = load_declared_native_channels(client, request, feature_attempt_id,
        producer_source_hash=producer_source_hash, authority=authority)
    scope = pl.from_arrow(packet.coverage).select(*DECISION_IDENTITY).unique()
    if left.join(scope, on=list(DECISION_IDENTITY), how='anti').height:
        raise ValueError('Decision identity is outside installed native coverage')
    left = left.with_columns(pl.lit(packet.feature_attempt_id).alias('feature_attempt_id'),
                            pl.lit(policy.inputs.digest).alias('policy_digest'))
    features = pl.from_arrow(packet.rows).filter(pl.col('resolution_ms') == policy.resolution_ms)
    value = pl.col(policy.channel)
    compare = {'lt': value < policy.threshold, 'le': value <= policy.threshold,
               'gt': value > policy.threshold, 'ge': value >= policy.threshold}[policy.comparison]
    eligible = features.select((value.is_not_null() & value.is_finite() & compare)
                              .fill_null(False).alias('eligible'))['eligible'].to_numpy()
    windows = completed_bar_sequence_windows(features, eligible, policy.sequence)
    result = qualify_completed_sequences_after_acquisition(windows, left, held, acquired,
        sequence_policy=policy.sequence, resolution_ms=policy.resolution_ms,
        decision_interval_ms=policy.inputs.decision_interval_ms,
        freshness_ms=dict(policy.inputs.freshness_by_resolution)[policy.resolution_ms],
        max_decisions=policy.max_decisions)
    if policy.reference_mode == 'below-first-post-acquisition-close':
        result = result & _reference_confirmation(features, left, acquired)
    return np.frombuffer(result.tobytes(), dtype=bool)
