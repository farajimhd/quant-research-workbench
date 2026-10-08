import numpy as np
import polars as pl
import pytest
from dataclasses import replace

from src.trading_runtime.native_channel_event_qualification import (
    NativeChannelAcquisitionQualificationPolicy,
    event_qualification_payload, qualify_native_channels_after_acquisition,
)
from tests.test_native_channel_qualification import matrix as channel_matrix
from src.trading_runtime.native_channel_qualification import NativeChannelBand


def matrix():
    aligned, channels = channel_matrix()
    return aligned, NativeChannelAcquisitionQualificationPolicy(channels, (60000,))


def test_completed_evidence_must_be_strictly_after_actual_fill():
    aligned, policy = matrix()
    events = np.array([359999999, 0, 400000000, 479999999, 600000000], dtype=np.int64)
    mask = np.ones(aligned.height, dtype=bool)
    assert qualify_native_channels_after_acquisition(aligned, mask, events, policy).tolist() == [True, False, False, True, False]
    events[0] = 360000000  # Fill exactly on a bar boundary cannot reuse that bar.
    events[3] = 480000000
    assert not qualify_native_channels_after_acquisition(aligned, mask, events, policy).any()


def test_no_held_ownership_cannot_become_exit_eligibility():
    aligned, policy = matrix()
    mask = np.zeros(aligned.height, dtype=bool)
    events = np.full(aligned.height, -1, dtype=np.int64)
    result = qualify_native_channels_after_acquisition(aligned, mask, events, policy)
    assert not result.any() and not result.flags.writeable
    with pytest.raises(ValueError):
        result.setflags(write=True)


@pytest.mark.parametrize('event', [-1, 360000001])
def test_missing_or_future_actual_acquisition_is_rejected(event):
    aligned, policy = matrix()
    mask = np.zeros(aligned.height, dtype=bool)
    mask[0] = True
    events = np.zeros(aligned.height, dtype=np.int64)
    events[0] = event
    with pytest.raises(ValueError, match='missing or later'):
        qualify_native_channels_after_acquisition(aligned, mask, events, policy)


def test_event_clock_is_exact_and_declared_rule_retains_parameters():
    aligned, policy = matrix()
    with pytest.raises(ValueError, match='event clock'):
        qualify_native_channels_after_acquisition(aligned, np.ones(aligned.height, dtype=bool),
            np.zeros(aligned.height, dtype=float), policy)
    payload = event_qualification_payload(policy)
    assert payload['channel_rule'] == policy.channels.payload()
    assert payload['fresh_resolutions_ms'] == [60000]
    assert 'actual acquisition' in payload['event_clock']


def test_slower_context_does_not_need_to_restart_after_acquisition():
    aligned, channels = channel_matrix()
    aligned = aligned.head(1).with_columns(pl.col('channels_60000ms').struct.with_fields(
        pl.lit(300000, dtype=pl.Int64).alias('available_day_boundary_ms'),
        pl.lit(60000, dtype=pl.Int64).alias('feature_age_ms')).alias('channels_300000ms'))
    inputs = replace(channels.inputs, resolutions_ms=(60000, 300000),
                     freshness_by_resolution=((60000, 59999), (300000, 299999)))
    channels = replace(channels, inputs=inputs, bands=(*channels.bands,
        NativeChannelBand(300000, 'close_return', 0.0, None)))
    policy = NativeChannelAcquisitionQualificationPolicy(channels, (60000,))
    mask = np.ones(1, dtype=bool)
    events = np.array([359999999], dtype=np.int64)
    assert qualify_native_channels_after_acquisition(aligned, mask, events, policy).tolist() == [True]
    both = replace(policy, fresh_resolutions_ms=(60000, 300000))
    assert not qualify_native_channels_after_acquisition(aligned, mask, events, both).any()


@pytest.mark.parametrize('resolutions', [(), (300000,), (60000, 60000)])
def test_trigger_resolutions_must_be_explicit_and_have_a_channel_band(resolutions):
    _, channels = channel_matrix()
    with pytest.raises(ValueError, match='trigger resolutions'):
        NativeChannelAcquisitionQualificationPolicy(channels, resolutions)
