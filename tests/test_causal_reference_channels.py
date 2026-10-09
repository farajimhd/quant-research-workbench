"""Causal reference projection, without native source-certification claims."""
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from research.causal_strategy_features.v6.reference import reference_channels


def sources():
    return pl.DataFrame({
        'symbol_id': ['A', 'A'], 'published_at_ms': [10, 20],
        'recorded_at_ms': [12, 22], 'inserted_at_ms': [15, 25],
        'identity_recorded_at_ms': [14, 24], 'snapshot_id': ['s1', 's2'],
        'source_hash': ['h1', 'h2'], 'shares': [100., 50.],
        'split_effective_at_ms': [40, 40], 'split_ratio': [0.5, 0.5],
    })


def align(source, clocks=(14, 15, 24, 25, 50)):
    return reference_channels(
        pl.DataFrame({'symbol_id': ['A'] * len(clocks), 'decision_at_ms': clocks}),
        source, value_columns=('shares', 'split_effective_at_ms', 'split_ratio'),
        max_age_ms=10, max_rows=100,
    )


def test_all_knowledge_clocks_freshness_and_future_effective_event():
    result = align(sources())
    assert result['reference_available'].to_list() == [False, True, True, True, False]
    assert result['shares'].to_list() == [None, 100., 100., 50., None]
    # Announcement knowledge does not imply effective status or price adjustment.
    assert result['split_effective_at_ms'].to_list() == [None, 40, 40, 40, None]
    assert result['snapshot_id'].to_list() == [None, 's1', 's1', 's2', None]


def test_future_snapshot_values_cannot_change_prefix():
    before = align(sources(), (14, 15, 24))
    changed = sources().with_columns(
        pl.when(pl.col('inserted_at_ms') > 24).then(999.).otherwise(pl.col('shares')).alias('shares'))
    assert_frame_equal(before, align(changed, (14, 15, 24)))


def test_identity_recording_delays_availability_and_symbols_are_isolated():
    source = sources().head(1).with_columns(pl.lit(30, dtype=pl.Int64).alias('identity_recorded_at_ms'))
    assert not align(source, (25,))['reference_available'][0]
    foreign = source.with_columns(pl.lit('B').alias('symbol_id'))
    assert not align(foreign, (30,))['reference_available'][0]


def test_ambiguous_snapshot_clock_rejected():
    with pytest.raises(ValueError, match='Ambiguous'):
        align(pl.concat([sources(), sources().head(1)]))


def test_missing_knowledge_clock_rejected():
    with pytest.raises(ValueError, match='missing'):
        align(sources().with_columns(pl.lit(None, dtype=pl.Int64).alias('inserted_at_ms')))


def test_declared_packet_bound_rejected():
    with pytest.raises(ValueError, match='bound'):
        reference_channels(pl.DataFrame({'symbol_id': ['A'], 'decision_at_ms': [20]}),
                           sources(), value_columns=('shares',), max_age_ms=10, max_rows=1)


def test_empty_reference_packet_preserves_decisions_as_unavailable():
    result = align(sources().head(0), (25, 15))
    assert result['decision_at_ms'].to_list() == [25, 15]
    assert result['reference_available'].to_list() == [False, False]
    assert result['shares'].to_list() == [None, None]


def test_unsorted_decisions_preserve_original_order():
    result = align(sources(), (25, 14, 15))
    assert result['shares'].to_list() == [50., None, 100.]
