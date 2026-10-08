import numpy as np
import polars as pl
import pytest

from src.trading_runtime.native_completed_bar_sequences import (
    CompletedBarSequencePolicy, completed_bar_sequence_windows,
)


def frame(buckets, **changes):
    size = len(buckets)
    values = dict(build_id=['build'] * size, session_date=['2026-08-04'] * size,
                  ticker=['TEST'] * size, attempt_id=['bars'] * size, feature_attempt_id=['attempt'] * size,
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


@pytest.mark.parametrize('field', ['ticker', 'session_date', 'build_id', 'attempt_id', 'feature_attempt_id', 'policy_digest'])
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


def test_backward_alignment_requires_actual_fill_freshness_and_held_ownership():
    from src.trading_runtime.native_completed_bar_sequences import qualify_completed_sequences_after_acquisition
    source = frame([0, 1, 2])
    prepared = windows(source)
    clocks = [119900, 120000, 120100, 120200, 180000]
    decisions = pl.concat([source.head(1)] * len(clocks)).select(
        'build_id', 'session_date', 'ticker', 'attempt_id', 'feature_attempt_id', 'policy_digest')
    decisions = decisions.with_columns(pl.Series('decision_day_ms', clocks, dtype=pl.Int64))
    held = np.array([True, True, True, True, False])
    acquired = np.array([0, 60000001, 0, 0, -1], dtype=np.int64)
    result = qualify_completed_sequences_after_acquisition(prepared, decisions, held, acquired,
        sequence_policy=CompletedBarSequencePolicy(2, 100), resolution_ms=60000,
        decision_interval_ms=100, freshness_ms=100, max_decisions=100)
    assert result.tolist() == [False, False, True, False, False]
    assert not result.flags.writeable
    order = [2, 0, 4, 3, 1]
    shuffled = pl.concat([decisions.slice(i, 1) for i in order])
    reordered = qualify_completed_sequences_after_acquisition(prepared, shuffled, held[order], acquired[order],
        sequence_policy=CompletedBarSequencePolicy(2, 100), resolution_ms=60000,
        decision_interval_ms=100, freshness_ms=100, max_decisions=100)
    assert reordered.tolist() == result[order].tolist()


def test_alignment_cannot_borrow_another_bars_attempt_or_accept_future_fill():
    from src.trading_runtime.native_completed_bar_sequences import qualify_completed_sequences_after_acquisition
    prepared = windows(frame([0, 1]))
    decisions = frame([0], attempt_id=['foreign']).select(
        'build_id', 'session_date', 'ticker', 'attempt_id', 'feature_attempt_id', 'policy_digest')
    decisions = decisions.with_columns(pl.lit(120000, dtype=pl.Int64).alias('decision_day_ms'))
    kwargs = dict(sequence_policy=CompletedBarSequencePolicy(2, 100), resolution_ms=60000,
                  decision_interval_ms=100, freshness_ms=100, max_decisions=100)
    assert not qualify_completed_sequences_after_acquisition(prepared, decisions, np.array([True]),
        np.array([0], dtype=np.int64), **kwargs)[0]
    with pytest.raises(ValueError, match='later than decision'):
        qualify_completed_sequences_after_acquisition(prepared, decisions, np.array([True]),
            np.array([120000001], dtype=np.int64), **kwargs)
