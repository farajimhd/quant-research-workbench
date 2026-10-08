from dataclasses import replace

import numpy as np
import polars as pl
import pytest

from research.causal_strategy_features.v4.native_bars import native_channels
from research.causal_strategy_features.v5.decisions import multi_resolution_decisions
from src.market_engine.native_causal_channel_contract import NativeChannelPolicy
from src.trading_runtime.native_channel_qualification import (
    NativeChannelBand, NativeChannelQualificationPolicy, qualify_native_channels,
)
from tests.test_causal_native_volatility_channels import source


def matrix():
    bars = native_channels(source(), resolutions_ms=(60000,), through_day_boundary_ms=600000,
                           lookback_bars=3, volatility_lookback_bars=3)
    decisions = bars.select('build_id', 'session_date', 'ticker', 'attempt_id').unique().join(
        pl.DataFrame({'decision_day_ms': [360000, 60000, 420000, 480000, 660000]}), how='cross')
    inputs = NativeChannelPolicy((60000,), 100, ((60000, 59999),), 3, 3)
    aligned = multi_resolution_decisions(bars, decisions, decision_interval_ms=100,
                                        freshness_by_resolution=inputs.freshness_by_resolution)
    return aligned, NativeChannelQualificationPolicy(inputs,
        (NativeChannelBand(60000, 'close_return', 0.0, None),))


def test_qualification_preserves_order_and_cannot_restore_mandatory_rejection():
    aligned, policy = matrix()
    all_allowed = np.ones(aligned.height, dtype=bool)
    # Rising, unavailable prior close, falling, rising, stale source respectively.
    assert qualify_native_channels(aligned, all_allowed, policy).tolist() == [True, False, False, True, False]
    all_allowed[0] = False
    assert qualify_native_channels(aligned, all_allowed, policy).tolist() == [False, False, False, True, False]
    assert aligned['decision_day_ms'].to_list() == [360000, 60000, 420000, 480000, 660000]


def test_future_labels_do_not_affect_qualification_or_its_declaration():
    aligned, policy = matrix()
    eligible = np.ones(aligned.height, dtype=bool)
    expected = qualify_native_channels(aligned, eligible, policy)
    changed = aligned.with_columns(pl.lit(1000000.0).alias('future_reward'))
    assert np.array_equal(expected, qualify_native_channels(changed, eligible, policy))
    with pytest.raises(ValueError, match='relative channel'):
        NativeChannelBand(60000, 'future_reward', 0.0, None)


def test_future_or_inconsistent_available_candle_is_rejected():
    aligned, policy = matrix()
    changed = aligned.with_columns(pl.col('channels_60000ms').struct.with_fields(
        pl.lit(86400000, dtype=pl.Int64).alias('available_day_boundary_ms')))
    with pytest.raises(ValueError, match='future or inconsistent'):
        qualify_native_channels(changed, np.ones(aligned.height, dtype=bool), policy)


def test_invalid_and_nonfinite_channel_never_qualify():
    aligned, policy = matrix()
    invalid = aligned.with_columns(pl.col('channels_60000ms').struct.with_fields(
        pl.lit(False).alias('candle_available')))
    assert not qualify_native_channels(invalid, np.ones(aligned.height, dtype=bool), policy).any()
    nonfinite = aligned.with_columns(pl.col('channels_60000ms').struct.with_fields(
        pl.lit(float('nan')).alias('close_return')))
    assert not qualify_native_channels(nonfinite, np.ones(aligned.height, dtype=bool), policy).any()


@pytest.mark.parametrize('bounds', [(None, None), (True, None), (float('nan'), None), (2.0, 1.0)])
def test_invalid_rule_bounds_fail(bounds):
    with pytest.raises(ValueError):
        NativeChannelBand(60000, 'close_return', *bounds)


def test_foreign_resolution_duplicate_rules_and_nonboolean_mask_fail():
    aligned, policy = matrix()
    with pytest.raises(ValueError, match='resolution'):
        replace(policy, bands=(NativeChannelBand(300000, 'close_return', 0.0, None),))
    with pytest.raises(ValueError, match='unique and ordered'):
        replace(policy, bands=policy.bands * 2)
    with pytest.raises(ValueError, match='Boolean'):
        qualify_native_channels(aligned, np.ones(aligned.height, dtype=np.uint8), policy)


def test_missing_structure_and_clock_mutation_fail_closed():
    aligned, policy = matrix()
    mask = np.ones(aligned.height, dtype=bool)
    with pytest.raises(ValueError, match='channel schema'):
        qualify_native_channels(aligned.drop('channels_60000ms'), mask, policy)
    with pytest.raises(ValueError, match='decision clock'):
        qualify_native_channels(aligned.with_columns(pl.col('decision_day_ms') + 1), mask, policy)
    payload = policy.payload()
    assert payload['input_policy_digest'] == policy.inputs.digest
    assert payload['input_policy']['freshness_by_resolution'] == ((60000, 59999),)
