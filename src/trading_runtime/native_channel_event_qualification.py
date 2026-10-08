"""Declared channel evidence after an actual acquisition event; no exit orders."""
import numpy as np
import polars as pl
from dataclasses import dataclass

from src.trading_runtime.native_channel_qualification import (
    NativeChannelQualificationPolicy, qualify_native_channels,
)

RULE = 'native-after-acquisition-channel-qualification@1'


@dataclass(frozen=True, slots=True)
class NativeChannelAcquisitionQualificationPolicy:
    channels: NativeChannelQualificationPolicy
    fresh_resolutions_ms: tuple[int, ...]

    def __post_init__(self):
        if type(self.channels) is not NativeChannelQualificationPolicy:
            raise ValueError('Typed native channel rule required')
        self.channels.__post_init__()
        resolutions = self.fresh_resolutions_ms
        if (type(resolutions) is not tuple or not resolutions or
                any(type(r) is not int for r in resolutions) or
                resolutions != tuple(sorted(set(resolutions))) or
                not set(resolutions).issubset({b.resolution_ms for b in self.channels.bands})):
            raise ValueError('Declared trigger resolutions must select qualified channels')


def event_qualification_payload(policy):
    if type(policy) is not NativeChannelAcquisitionQualificationPolicy:
        raise ValueError('Typed acquisition qualification rule required')
    policy.__post_init__()
    return dict(rule=RULE, channel_rule=policy.channels.payload(),
                fresh_resolutions_ms=list(policy.fresh_resolutions_ms),
                event_clock='actual acquisition microseconds since source midnight',
                evidence='completed channel boundary strictly after actual acquisition')


def qualify_native_channels_after_acquisition(aligned, mandatory_held,
        acquisition_day_us, policy):
    """Filter held rows using newly completed evidence in original row order.

    Portfolio/OMS must supply actual acquired ownership and fill clocks, rather
    than proposal or reservation time. This pure predicate cannot establish
    that ownership, certify source inputs, authorize an exit, or replace
    quote-confirmed risk checks and independent lot protection.
    """
    event_qualification_payload(policy)
    if (type(aligned) is not pl.DataFrame or type(acquisition_day_us) is not np.ndarray
            or acquisition_day_us.dtype != np.dtype(np.int64)
            or acquisition_day_us.shape != (aligned.height,)):
        raise ValueError('Exact acquired event clock columns required')
    if (type(mandatory_held) is not np.ndarray or mandatory_held.dtype != np.dtype(bool)
            or mandatory_held.shape != (aligned.height,)):
        raise ValueError('Exact original held ownership mask required')
    held = np.frombuffer(mandatory_held.tobytes(), dtype=bool)
    events = np.frombuffer(acquisition_day_us.tobytes(), dtype=np.int64)
    # The base rule validates the source clock, available rows,
    # freshness and finite channels before any event-clock arithmetic.
    qualified = qualify_native_channels(aligned, held, policy.channels)
    now = aligned['decision_day_ms'].to_numpy() * 1000
    if np.any(held & ((events < 0) | (events > now))):
        raise ValueError('Acquired event is missing or later than decision')
    guard = pl.Series('_acquired_day_us', events)
    conditions = [pl.Series('_qualified', qualified)]
    for resolution in policy.fresh_resolutions_ms:
        end = pl.col(f'channels_{resolution}ms').struct.field('available_day_boundary_ms')
        conditions.append((end * 1000 > guard).fill_null(False))
    result = aligned.select(pl.all_horizontal(conditions).alias('eligible'))['eligible'].to_numpy()
    return np.frombuffer(result.tobytes(), dtype=bool)
