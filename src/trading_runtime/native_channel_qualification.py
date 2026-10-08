"""Declared columnar feature qualification; no cash or execution admission."""
from dataclasses import dataclass
from math import isfinite

import numpy as np
import polars as pl

from src.market_engine.native_causal_channel_contract import NativeChannelPolicy, RELATIVE_FIELDS
from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY, MAX_EXPANDED_DECISIONS

RULE = 'native-completed-channel-qualification@1'


@dataclass(frozen=True, slots=True)
class NativeChannelBand:
    resolution_ms: int
    channel: str
    lower: float | None
    upper: float | None

    def __post_init__(self):
        if type(self.resolution_ms) is not int or type(self.channel) is not str or self.channel not in RELATIVE_FIELDS:
            raise ValueError('Declared native relative channel required')
        bounds = (self.lower, self.upper)
        if all(v is None for v in bounds) or any(v is not None and
                (type(v) is not float or not isfinite(v)) for v in bounds):
            raise ValueError('Finite Float64 qualification bound required')
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError('Qualification bounds are reversed')


@dataclass(frozen=True, slots=True)
class NativeChannelQualificationPolicy:
    inputs: NativeChannelPolicy
    bands: tuple[NativeChannelBand, ...]

    def __post_init__(self):
        if type(self.inputs) is not NativeChannelPolicy or type(self.bands) is not tuple or not 1 <= len(self.bands) <= 32:
            raise ValueError('Typed bounded native qualification declaration required')
        self.inputs.__post_init__()
        for band in self.bands:
            if type(band) is not NativeChannelBand:
                raise ValueError('Exact native channel band required')
            band.__post_init__()
            if band.resolution_ms not in self.inputs.resolutions_ms:
                raise ValueError('Qualification resolution is not declared')
        keys = [(b.resolution_ms, b.channel) for b in self.bands]
        if keys != sorted(set(keys)):
            raise ValueError('Qualification bands must be unique and ordered')

    def payload(self):
        self.__post_init__()
        return dict(rule=RULE, input_policy=self.inputs.payload(), input_policy_digest=self.inputs.digest,
            bands=[dict(resolution_ms=b.resolution_ms, channel=b.channel,
                        lower=b.lower, upper=b.upper) for b in self.bands])


def qualify_native_channels(aligned, mandatory_eligible, policy):
    """Intersect declared bands with mandatory eligibility in original row order.

    Installed source, complete causal candidate population, release preflight,
    VWAP, liquidity and structural admission remain mandatory caller contracts.
    Input is the completed multi-resolution matrix, never hindsight labels.
    No feature source queries, market-row loops, ranking or sequential state are
    introduced. Unavailable, invalid or stale candles cannot qualify.
    """
    if type(aligned) is not pl.DataFrame or type(policy) is not NativeChannelQualificationPolicy:
        raise ValueError('Typed columnar native qualification input required')
    policy.__post_init__()
    if (type(mandatory_eligible) is not np.ndarray or mandatory_eligible.dtype != np.dtype(bool)
            or mandatory_eligible.shape != (aligned.height,)):
        raise ValueError('Exact Boolean mandatory eligibility mask required')
    if aligned.height * len(policy.inputs.resolutions_ms) > MAX_EXPANDED_DECISIONS:
        raise ValueError('Native qualification packet exceeds declared bound')
    if not set((*DECISION_IDENTITY, 'decision_day_ms')).issubset(aligned.columns):
        raise ValueError('Native qualification decision identity is missing')
    identities = aligned.select(*DECISION_IDENTITY, 'decision_day_ms')
    if (any(identities[c].null_count() for c in identities.columns) or
            identities.n_unique() != identities.height or
            identities.schema['decision_day_ms'] != pl.Int64 or
            identities.filter((pl.col('decision_day_ms') < 0) | (pl.col('decision_day_ms') > 86400000) |
                (pl.col('decision_day_ms') % policy.inputs.decision_interval_ms != 0)).height):
        raise ValueError('Native qualification identity or decision clock differs')
    conditions = [pl.Series('_mandatory_eligible', mandatory_eligible)]
    freshness = dict(policy.inputs.freshness_by_resolution)
    for band in policy.bands:
        name = f'channels_{band.resolution_ms}ms'
        schema = aligned.schema.get(name)
        required = dict(feature_row_available=pl.Boolean, candle_available=pl.Boolean,
                        feature_age_ms=pl.Int64, available_day_boundary_ms=pl.Int64)
        required[band.channel] = pl.Float64
        if not isinstance(schema, pl.Struct) or any(schema.to_schema().get(k) != t for k, t in required.items()):
            raise ValueError('Native qualification channel schema differs')
        channel = pl.col(name)
        available = channel.struct.field('feature_row_available').fill_null(False)
        end = channel.struct.field('available_day_boundary_ms')
        age = channel.struct.field('feature_age_ms')
        malformed = available & ((end > pl.col('decision_day_ms')) | (end < 0) |
            (end % band.resolution_ms != 0) |
            (age != pl.col('decision_day_ms') - end) | end.is_null() | age.is_null())
        if aligned.filter(malformed).height:
            raise ValueError('Native qualification availability is future or inconsistent')
        value = channel.struct.field(band.channel)
        condition = available & channel.struct.field('candle_available').fill_null(False) & \
            (age <= freshness[band.resolution_ms]) & value.is_not_null() & value.is_finite()
        if band.lower is not None:
            condition &= value >= band.lower
        if band.upper is not None:
            condition &= value <= band.upper
        conditions.append(condition.fill_null(False))
    return aligned.select(pl.all_horizontal(conditions).alias('eligible'))['eligible'].to_numpy()
