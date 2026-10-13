"""Published native seals compose into the existing app preparation funnel."""
import asyncio
from dataclasses import replace
from datetime import datetime, UTC, time
from types import MappingProxyType

import pytest

from src.backend import backtest_program_input_plan as plans
from src.backend import program_app_preparation as flow
from src.backend import program_configuration_selection_service as selection
from src.backend.qmd_columnar_history import HistoryPinRequest
from src.backend.replay_run_service import ReplayRunController
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.market_engine import columnar_history_product as c
from tests.test_program_controller_runtime import definition_and_actor
from tests.test_backtest_program_inputs import release_for
from test_columnar_history_publication import Store, Client, publish, consume, prepared_source


@pytest.fixture
def published(prepared_source, tmp_path):
    feature_store, volume_store = Store(), Store()
    publish(feature_store, prepared_source)
    volume_source = replace(prepared_source, fields=())
    publish(volume_store, volume_source)
    feature, volume = consume(feature_store, prepared_source), consume(volume_store, volume_source)
    release = release_for(feature.pin())
    opening = replace(prepared_source.opening_reference, rows_by_listing=MappingProxyType({
        listing: MappingProxyType({**row, 'status': 'resolved', 'ticker': 'ABC',
            'listing_id': listing, 'adjusted_prior_close': 10.,
            'available_at': datetime(2026, 8, 3, 8, tzinfo=UTC)})
        for listing, row in prepared_source.opening_reference.rows_by_listing.items()}))
    definition, actor = definition_and_actor()
    definition = replace(definition, session_date=feature.prepared.scope.session_date,
        start_time=time(9, 31, 20), end_time=time(9, 31, 30))
    actor = AssignedProgramStrategy(release, actor.assignments(), actor._listings)
    controller = ReplayRunController(definition, runtime_root=tmp_path, program_strategy=actor,
        program_liquidity_resolution_ms=100)
    def request(product):
        return HistoryPinRequest(product.prepared.scope, product.prepared.fields,
            product.parents, product.header['content_hash'])
    source = plans.ProgramPublishedInputRequest(opening, (request(feature),), (request(volume),),
        0, 200, 'optimizer-origin', ((34280, 34290),),
        c.completed_clock(feature.prepared.scope, feature.prepared.scope.stop_bucket - 1), 2)
    class Reader:
        def execute(self, query):
            store = volume_store if volume.prepared.scope.product_revision in query else feature_store
            return Client(store).execute(query)
        def close(self):
            pass
    return controller, source, Reader


def test_native_published_seals_become_existing_cohort_plans(published):
    controller, request, reader = published
    cohorts = asyncio.run(plans.certify_program_session_input_plan(controller, request, reader_factory=reader))
    assert len(cohorts) == 1 and type(cohorts[0]) is plans.ProgramCohortInputPlan
    assert cohorts[0].first_bucket == 34280 and cohorts[0].stop_bucket == 34290
    assert cohorts[0].evaluation_block == 'optimizer-origin'
    assert cohorts[0].population_pin.rule == controller._program_strategy.program_release.universe
    assert controller._journal is None and controller._runtime is None and controller._task is None


@pytest.mark.parametrize('mutation', ['population', 'window', 'gap', 'fields'])
def test_invalid_published_plan_fails_before_reader_or_writer(published, mutation):
    controller, request, _ = published
    if mutation == 'population': request = replace(request, volume_requests=())
    elif mutation == 'window': request = replace(request, cohort_windows=((34280, 34291),))
    elif mutation == 'gap': request = replace(request, cohort_windows=((34280, 34284), (34285, 34290)))
    else: request = replace(request, feature_requests=request.volume_requests)
    def forbidden():
        pytest.fail('Opened reader for an invalid published plan')
    with pytest.raises(ValueError):
        asyncio.run(plans.certify_program_session_input_plan(controller, request, reader_factory=forbidden))
    assert controller._journal is None


def test_app_reserves_handoff_then_cold_loads_before_native_funnel(published, monkeypatch):
    controller, request, reader = published
    selector = selection.ProgramConfigurationSelectionService(
        approved_selections={'00000000-0000-0000-0000-000000000001': 'a' * 64},
        selection_client_factory=reader, release_client_factory=reader,
        policy_client_factory=reader, policy_dispatch=object(), concurrency=2)
    monkeypatch.setattr(selection, '_SERVICE', selector)
    calls = []
    async def prepare(*args, **kwargs):
        assert selector._preparing
        calls.append('controller')
        return controller
    original = plans.certify_program_session_input_plan
    async def load(*args, **kwargs):
        assert selector._preparing and selector._preparation_controller is controller
        calls.append('cold-inputs')
        return await original(*args, **kwargs)
    async def complete(service, actual, **kwargs):
        assert actual is controller and type(kwargs['cohorts'][0]) is plans.ProgramCohortInputPlan
        calls.append('native-funnel')
        return controller
    monkeypatch.setattr(selector, 'prepare_controller', prepare)
    monkeypatch.setattr(plans, 'certify_program_session_input_plan', load)
    monkeypatch.setattr(flow, 'complete_program_app_preparation', complete)
    result = asyncio.run(selector.prepare_app_controller(object(), controller.definition,
        assignments=controller._program_strategy.assignments(), listing_by_assignment=controller._program_strategy._listings,
        liquidity_resolution_ms=100, cohorts=request, scopes=({},), journal_scope={},
        signal_publisher=object(), signal_scope={}, checkpoint_connection_factory=reader, checkpoint_concurrency=2))
    assert result is controller and calls == ['controller', 'cold-inputs', 'native-funnel']
    assert not selector._preparing
