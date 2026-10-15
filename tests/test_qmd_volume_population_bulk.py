"""Bulk population projection preserves the existing certified scalar reader."""
import asyncio
from datetime import datetime, timezone

import pytest

from tests.test_qmd_volume_population_integration import population_fixture, prepared_source
from src.backend.qmd_columnar_population import (
    read_population_window, _population_listing_frame, _population_window,
)
from src.backend.qmd_volume_reader import volume_window

AS_OF = datetime(2026, 8, 4, tzinfo=timezone.utc)


def legacy(population, first, stop):
    frames = {listing: _population_listing_frame(population, listing,
        volume_window(history, first_ordinal=first - history.scope.first_bucket,
            stop_ordinal=stop - history.scope.first_bucket, as_of=AS_OF))
        for listing, history in sorted(population.histories.items())}
    return _population_window(population, frames)


def current(population, first, stop, **kwargs):
    return asyncio.run(read_population_window(population, first_bucket=first, stop_bucket=stop,
        as_of=kwargs.get('as_of', AS_OF), concurrency=2))


@pytest.mark.parametrize('offset,width', [(0, 63), (83, 63), (182, 18), (0, 200)])
def test_bulk_matches_scalar_reader_rows_bits_ranks_and_membership(population_fixture, offset, width):
    feature, population, _ = population_fixture
    first = feature.scope.first_bucket + offset
    expected = legacy(population, first, first + width)
    actual = current(population, first, first + width)
    assert actual.rows.schema == expected.rows.schema
    assert actual.rows.equals(expected.rows)
    for field in ('rolling_volume', 'adjusted_prior_close'):
        assert (actual.rows[field].to_numpy().view('uint64') ==
                expected.rows[field].to_numpy().view('uint64')).all()
    assert actual.ranked.schema == expected.ranked.schema
    assert actual.ranked.equals(expected.ranked)
    assert actual._membership == expected._membership
    assert actual.source_revision == expected.source_revision
    assert actual.rows.height == 2 * width
    # Complete zero-volume origins stay present even though the scanner excludes them.
    if offset == 0:
        assert actual.rows.filter(actual.rows['listing_id'] == 'listing-b')['rolling_volume'].min() == 0.


@pytest.mark.parametrize('corruption', ['empty', 'duplicate', 'search_index', 'nan', 'negative'])
def test_bulk_preserves_existing_malformed_private_pin_rejection(population_fixture, corruption):
    feature, population, _ = population_fixture
    history = population.histories['listing']
    if corruption == 'empty':
        object.__setattr__(history, 'intervals', ())
    elif corruption == 'duplicate':
        object.__setattr__(history, 'intervals', (history.intervals[0],) + history.intervals)
    elif corruption == 'search_index':
        object.__setattr__(history, '_starts', tuple(999999 for _ in history._starts))
    else:
        object.__setattr__(history.intervals[0], 'rolling_volume',
            float('nan') if corruption == 'nan' else -1.)
    first, stop = feature.scope.first_bucket, feature.scope.stop_bucket
    with pytest.raises(ValueError):
        legacy(population, first, stop)
    with pytest.raises(ValueError):
        current(population, first, stop)


def test_bulk_future_and_width_fail_before_projection(population_fixture):
    feature, population, _ = population_fixture
    first = feature.scope.first_bucket
    with pytest.raises(ValueError, match='future'):
        current(population, first, first + 63,
            as_of=datetime(2026, 8, 3, 13, 30, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match='bounds'):
        current(population, first, first + 4097)

@pytest.mark.parametrize('all_zero', [False, True])
def test_bulk_keeps_zero_origins_and_existing_string_identity_ties(population_fixture, all_zero):
    from dataclasses import replace
    from tests.test_qmd_volume_population_integration import volume_pin
    _, population, _ = population_fixture
    histories = {listing: volume_pin(pin.source, [0. if all_zero else 10.] * pin.scope.clock_count)
        for listing, pin in population.histories.items()}
    population = replace(population, histories=histories, rule=replace(population.rule, top_count=1))
    first = next(iter(histories.values())).scope.first_bucket
    actual, expected = current(population, first, first + 63), legacy(population, first, first + 63)
    assert actual.rows.equals(expected.rows) and actual.ranked.equals(expected.ranked)
    assert actual.rows.height == 126
    if all_zero:
        assert actual.ranked.is_empty()
    else:
        assert actual.ranked['listing_id'].to_list() == ['listing'] * 63


def test_bulk_uses_each_pin_absolute_bucket_origin(population_fixture):
    from dataclasses import replace
    from tests.test_qmd_volume_population_integration import volume_pin
    _, population, _ = population_fixture
    histories = dict(population.histories)
    old = histories['listing-b']
    source = replace(old.source, first_bucket=old.scope.first_bucket + 10)
    histories['listing-b'] = volume_pin(source, [20.] * (old.scope.clock_count - 10))
    population = replace(population, histories=histories)
    first = old.scope.first_bucket + 20
    actual, expected = current(population, first, first + 63), legacy(population, first, first + 63)
    assert actual.rows.equals(expected.rows) and actual.ranked.equals(expected.ranked)
