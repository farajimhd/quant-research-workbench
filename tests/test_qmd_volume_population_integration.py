"""Complete volume census with a proven sparse program feature funnel."""
import asyncio
from dataclasses import replace
from datetime import datetime, time, timezone
from types import MappingProxyType

import polars as pl
import pytest

from pipelines.market_sip.events.qmd_volume_producer import CertifiedVolumeSource
from src.backend.qmd_volume_reader import CertifiedVolumePin, VolumePinRequest
from src.backend.qmd_columnar_history import HistoryPinRequest
from src.backend.qmd_columnar_population import (
    CertifiedVolumePopulationPin, read_population_window, certify_population_history_async,
)
from src.backend.backtest_program_population_cache import ProgramPopulationCache
from src.backend.backtest_program_inputs import preflight_program_cohort_inputs
from src.backend.backtest_program_input_plan import ProgramPublishedInputRequest, certify_program_session_input_plan
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.market_engine import qmd_volume_product as v
from tests.test_columnar_history_publication import prepared_source, Store, publish, consume
from tests.test_backtest_program_inputs import release_for
from tests.test_program_controller_runtime import definition_and_actor


CODE = '5' * 64


def no_database():
    pytest.fail('Snapshot projection opened a database reader')


def test_volume_census_reuses_reader_between_listings(population_fixture, monkeypatch):
    from src.backend import qmd_volume_reader as reader
    from pipelines.market_sip.events import qmd_volume_producer as producer
    _, population, _ = population_fixture
    clients, checked = [], []
    class Client:
        closed = False
        def close(self):
            assert not self.closed
            self.closed = True
    def factory():
        client = Client()
        clients.append(client)
        return client
    pins = population.histories
    requests = tuple(VolumePinRequest(pin.scope, pin.parents, pin.header['content_hash'], pin.source)
                     for pin in pins.values())
    monkeypatch.setattr(reader, 'permission_preflight', lambda client: None)
    monkeypatch.setattr(reader, 'storage_preflight', lambda client, **kwargs: None)
    monkeypatch.setattr(producer, 'verify_volume_sources', lambda client, sources: checked.append(sources))
    def read(client, request):
        assert not client.closed
        return pins[request.scope.listing_id]
    monkeypatch.setattr(reader, '_read_certified_pin', read)
    result = asyncio.run(reader.certify_volume_pins_async(factory, requests, concurrency=1))
    assert dict(result) == dict(pins)
    assert len(checked) == 2 and checked[0] == checked[1] == tuple(r.source for r in requests)
    assert len(clients) == 3 and all(client.closed for client in clients)


def volume_pin(source, values):
    scope = source.scope('00000000-0000-0000-0000-000000000091', CODE)
    parents = source.parents(CODE)
    bars = pl.DataFrame({'bucket_index': pl.Series(range(scope.first_bucket, scope.stop_bucket), dtype=pl.UInt32),
                         'volume': values})
    rows = v.prepare_intervals(scope, bars, parents)
    header = v.seal(scope, parents, rows, certified_at=datetime(2026, 8, 4, tzinfo=timezone.utc))
    return CertifiedVolumePin(scope, parents, header, source, v.interval_runs(rows))


@pytest.fixture
def population_fixture(prepared_source):
    store = Store()
    publish(store, prepared_source)
    feature = consume(store, prepared_source).pin()
    base = prepared_source.opening_source
    market = replace(base.market, tickers=('ABC', 'DEF'), units=base.market.units + tuple(
        replace(u, ticker='DEF') for u in base.market.units))
    prior = replace(base.prior_market, tickers=('ABC', 'DEF'), units=base.prior_market.units + tuple(
        replace(u, ticker='DEF') for u in base.prior_market.units))
    listing = replace(base.listings[0], ticker='DEF', listing_id='listing-b')
    opening_source = replace(base, market=market, prior_market=prior,
        identity=replace(base.identity, tickers=('ABC', 'DEF'), conids=(1, 2)), listings=base.listings + (listing,))
    available = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    rows = {listing.listing_id: MappingProxyType(dict(status='resolved', listing_id=listing.listing_id,
        ticker=listing.ticker, adjusted_prior_close=10., available_at=available))
        for listing in opening_source.listings}
    opening = replace(prepared_source.opening_reference,
        scope=opening_source.scope(prepared_source.opening_reference.scope.producer_code_hash),
        rows_by_listing=MappingProxyType(rows))
    sources = tuple(CertifiedVolumeSource(opening_source, opening, ticker, feature.scope.first_bucket,
        feature.scope.stop_bucket, 30) for ticker in ('ABC', 'DEF'))
    pins = (volume_pin(sources[0], [10.] * 200), volume_pin(sources[1], [0.] * 100 + [20.] * 100))
    release = release_for(feature)
    population = CertifiedVolumePopulationPin(opening, {p.scope.listing_id: p for p in pins},
                                               release.universe, no_database)
    return feature, population, release


def test_complete_volume_population_rejects_missing_listing_and_wrong_build(population_fixture):
    _, population, _ = population_fixture
    with pytest.raises(ValueError, match='complete price-eligible population'):
        replace(population, histories={'listing': population.histories['listing']})
    with pytest.raises(ValueError, match='population lineage'):
        replace(population, opening=replace(population.opening,
            scope=replace(population.opening.scope, build_id='different-build')))
    with pytest.raises(TypeError):
        population.histories['new'] = population.histories['listing']


def test_snapshot_population_and_cache_expand_only_requested_clocks_without_database(population_fixture):
    feature, population, _ = population_fixture
    retained = sum(p.retained_interval_bytes for p in population.histories.values())
    cache = ProgramPopulationCache(maximum_retained_bytes=retained)
    async def run():
        kwargs = dict(as_of=datetime(2026, 8, 4, tzinfo=timezone.utc), concurrency=2)
        first = await cache.load(population, first_bucket=feature.scope.first_bucket,
            stop_bucket=feature.scope.first_bucket + 50, **kwargs)
        assert first.rows.height == 100 and set(first.ranked['listing_id']) == {'listing'}
        later = await cache.load(population, first_bucket=feature.scope.first_bucket + 150,
            stop_bucket=feature.scope.stop_bucket, **kwargs)
        assert later.rows.height == 100 and set(later.ranked['listing_id']) == {'listing', 'listing-b'}
        assert cache.retained_bytes == retained and cache._frames is None
        with pytest.raises(ValueError, match='retained-byte budget'):
            await ProgramPopulationCache(maximum_retained_bytes=retained - 1).load(population,
                first_bucket=feature.scope.first_bucket, stop_bucket=feature.scope.first_bucket + 50, **kwargs)
        with pytest.raises(ValueError, match='future'):
            await cache.load(population, first_bucket=feature.scope.first_bucket,
                stop_bucket=feature.scope.first_bucket + 50,
                as_of=first.rows['boundary_at'].min(), concurrency=2)
    asyncio.run(run())


@pytest.mark.parametrize('include_later_ranked_listing', [False, True])
def test_published_sparse_feature_funnel_requires_full_requested_ranked_union(
        population_fixture, tmp_path, monkeypatch, include_later_ranked_listing):
    feature, population, release = population_fixture
    definition, original = definition_and_actor()
    end = time(9, 33, 20) if include_later_ranked_listing else time(9, 31, 40)
    definition = replace(definition, session_date=feature.scope.session_date, start_time=time(9, 30), end_time=end)
    actor = AssignedProgramStrategy(release, original.assignments(), {'assignment': 'listing'})
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
                                    program_liquidity_resolution_ms=100)
    feature_request = HistoryPinRequest(feature.scope, feature.fields, feature.parents, feature.header['content_hash'])
    requests = tuple(VolumePinRequest(p.scope, p.parents, p.header['content_hash'], p.source)
                     for p in population.histories.values())
    stop = feature.scope.stop_bucket if include_later_ranked_listing else feature.scope.first_bucket + 100
    request = ProgramPublishedInputRequest(population.opening, (feature_request,), requests, 0, 200,
        'certified-origin', ((feature.scope.first_bucket, stop),),
        datetime(2026, 8, 4, tzinfo=timezone.utc), 2, maximum_retained_population_bytes=1024 * 1024)
    feature_reads = []
    async def certify_volumes(factory, packet, **kwargs):
        assert packet == requests
        assert kwargs['maximum_retained_bytes'] == request.maximum_retained_population_bytes
        return population.histories
    async def certify_features(factory, packet, **kwargs):
        feature_reads.append(packet)
        return {'listing': feature}
    monkeypatch.setattr('src.backend.qmd_volume_reader.certify_volume_pins_async', certify_volumes)
    monkeypatch.setattr('src.backend.qmd_columnar_history.certify_history_pins_async', certify_features)
    if include_later_ranked_listing:
        with pytest.raises(ValueError, match='omits ranked listings'):
            asyncio.run(certify_program_session_input_plan(controller, request, reader_factory=no_database))
        assert feature_reads == []
    else:
        plans = asyncio.run(certify_program_session_input_plan(controller, request, reader_factory=no_database))
        assert len(plans) == 1 and set(plans[0].pins) == {'listing'}
        assert set(plans[0].population_pin.histories) == {'listing', 'listing-b'}
        assert len(feature_reads) == 1


def test_cold_population_admission_rejects_missing_volume_before_read(population_fixture):
    _, population, release = population_fixture
    pin = population.histories['listing']
    request = VolumePinRequest(pin.scope, pin.parents, pin.header['content_hash'], pin.source)
    with pytest.raises(ValueError, match='complete price-eligible population'):
        asyncio.run(certify_population_history_async(population.opening, release.universe, (request,),
            reader_factory=no_database, concurrency=1))


def test_feature_volume_common_bar_lineage_remains_exact_without_liquidity_parent(population_fixture):
    feature, population, release = population_fixture
    kwargs = dict(first_ordinal=0, stop_ordinal=200, evaluation_block='certified-origin',
        first_bucket=feature.scope.first_bucket, stop_bucket=feature.scope.first_bucket + 100,
        as_of=datetime(2026, 8, 4, tzinfo=timezone.utc), concurrency=1)
    preflight_program_cohort_inputs(release, {'listing': feature}, population,
                                   {'assignment': 'listing'}, **kwargs)
    source = population.histories['listing'].source
    market = source.opening_source.market
    changed = replace(market, units=tuple(replace(u, output_hash='9' * 64)
        if u.stage == 'bars' and u.ticker == 'ABC' else u for u in market.units))
    changed_source = replace(source, opening_source=replace(source.opening_source, market=changed))
    changed_pin = volume_pin(changed_source, [10.] * 200)
    mismatched = replace(population, histories={**population.histories, 'listing': changed_pin})
    with pytest.raises(ValueError, match='complete scanner lineage'):
        preflight_program_cohort_inputs(release, {'listing': feature}, mismatched,
                                       {'assignment': 'listing'}, **kwargs)
