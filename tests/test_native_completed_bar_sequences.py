import numpy as np
import polars as pl
import pytest

from src.trading_runtime.native_completed_bar_sequences import (
    CompletedBarSequencePolicy, completed_bar_sequence_windows,
)


def frame(buckets, **changes):
    size = len(buckets)
    values = dict(build_id=['build'] * size, session_date=['2026-08-04'] * size,
                  ticker=['TEST'] * size, feature_attempt_id=['attempt'] * size,
                  policy_digest=['policy'] * size, resolution_ms=[60000] * size,
                  bucket_index=buckets, available_day_boundary_ms=[(i + 1) * 60000 for i in buckets],
                  candle_available=[True] * size)
    values.update(changes)
    return pl.DataFrame(values, schema_overrides=dict(
        resolution_ms=pl.UInt32, bucket_index=pl.UInt32,
        available_day_boundary_ms=pl.Int64, candle_available=pl.Boolean))


def windows(data, mask=None, count=2):
    mask = np.ones(data.height, dtype=bool) if mask is None else np.array(mask, dtype=bool)
    return completed_bar_sequence_windows(data, mask, CompletedBarSequencePolicy(count, 100))


def test_gaps_missing_predicates_and_unavailable_bars_break_support():
    assert windows(frame([0, 2, 3]))['sequence_eligible'].to_list() == [False, False, True]
    assert windows(frame([0, 1, 2]), [True, False, True])['sequence_eligible'].to_list() == [False] * 3
    data = frame([0, 1, 2], candle_available=[True, False, True])
    assert windows(data)['sequence_eligible'].to_list() == [False] * 3


@pytest.mark.parametrize('field', ['ticker', 'session_date', 'build_id', 'feature_attempt_id', 'policy_digest'])
def test_support_does_not_cross_source_or_ticker_identity(field):
    assert windows(frame([0, 1], **{field: ['first', 'second']}))['sequence_eligible'].to_list() == [False, False]


def test_input_order_is_preserved_and_appended_future_cannot_change_prefix():
    data = frame([1, 0, 2])
    output = windows(data)
    assert output['bucket_index'].to_list() == [1, 0, 2]
    assert output['sequence_eligible'].to_list() == [True, False, True]
    extended = windows(pl.concat([data, frame([3, 4])]))
    assert extended.head(data.height).equals(output)


def test_earliest_supporting_completion_excludes_prefill_evidence():
    output = windows(frame([0, 1, 2]))
    assert output['sequence_start_day_ms'].to_list() == [None, 60000, 120000]
    # Fill at 60,000,001 us: the first two-bar window reaches before the fill.
    actual_fill_us = 60000001
    eligible = (output['sequence_eligible'] &
                (output['sequence_start_day_ms'] * 1000 > actual_fill_us)).fill_null(False)
    assert eligible.to_list() == [False, False, True]


def test_resolution_and_minimum_count_are_parameters():
    data = frame([0, 1, 2], resolution_ms=[30000] * 3,
                 available_day_boundary_ms=[30000, 60000, 90000])
    assert windows(data, count=3)['sequence_eligible'].to_list() == [False, False, True]
    assert windows(data, count=1)['sequence_eligible'].to_list() == [True] * 3


def test_empty_typed_source_has_no_windows():
    output = windows(frame([0]).head(0))
    assert output.height == 0
    assert output.schema['sequence_eligible'] == pl.Boolean
    assert output.schema['sequence_start_day_ms'] == pl.Int64


def test_duplicates_off_grid_null_identity_and_bad_masks_fail_closed():
    for data in (frame([0, 0]), frame([0], available_day_boundary_ms=[59999]),
                 frame([0], ticker=[None])):
        with pytest.raises(ValueError):
            windows(data)
    with pytest.raises(ValueError):
        completed_bar_sequence_windows(frame([0]), np.array([1]), CompletedBarSequencePolicy(2, 10))


@pytest.mark.parametrize('count', [False, 0, -1, 1.0, 11])
def test_policy_cannot_invent_or_coerce_parameters(count):
    with pytest.raises(ValueError):
        CompletedBarSequencePolicy(count, 10)
