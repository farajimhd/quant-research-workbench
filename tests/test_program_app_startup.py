import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src.backend import program_configuration_selection_service as selection
from src.backend.program_app_startup import configure_program_app_startup, start_program_app_selection
from tests.test_program_configuration_selection_service import installed


def supplied(monkeypatch):
    service, revision, reads = installed(monkeypatch)
    monkeypatch.setattr(selection, '_SERVICE', None)
    application = configure_program_app_startup(FastAPI(), service)
    return application, service, revision, reads


def test_default_startup_is_inert(monkeypatch):
    monkeypatch.setattr(selection, '_SERVICE', None)
    assert asyncio.run(start_program_app_selection(FastAPI())) == 0
    assert selection.program_configuration_selection_service() is None


def test_central_verification_precedes_installation_and_restart_rechecks(monkeypatch):
    application, service, revision, reads = supplied(monkeypatch)
    original = selection.load_certified_program_configuration
    async def verified(**kwargs):
        assert selection.program_configuration_selection_service() is None
        return await original(**kwargs)
    monkeypatch.setattr(selection, 'load_certified_program_configuration', verified)
    assert asyncio.run(start_program_app_selection(application)) == 1
    assert selection.program_configuration_selection_service() is service
    assert len(reads) == 1 and reads[0]['expected_content_hash'] == revision['selection_hash']
    monkeypatch.setattr(selection, 'load_certified_program_configuration', original)
    assert asyncio.run(start_program_app_selection(application)) == 1
    assert len(reads) == 2


@pytest.mark.parametrize('error', [ValueError('Invalid central seal'), asyncio.CancelledError()])
def test_failed_or_cancelled_read_does_not_install(monkeypatch, error):
    application, _, _, _ = supplied(monkeypatch)
    async def fail(**kwargs):
        raise error
    monkeypatch.setattr(selection, 'load_certified_program_configuration', fail)
    with pytest.raises(type(error)):
        asyncio.run(start_program_app_selection(application))
    assert selection.program_configuration_selection_service() is None


def test_startup_owner_change_blocks_installation(monkeypatch):
    application, _, _, _ = supplied(monkeypatch)
    original = selection.load_certified_program_configuration
    async def change(**kwargs):
        result = await original(**kwargs)
        application.state.approved_program_selection_startup = None
        return result
    monkeypatch.setattr(selection, 'load_certified_program_configuration', change)
    with pytest.raises(RuntimeError, match='owner changed'):
        asyncio.run(start_program_app_selection(application))
    assert selection.program_configuration_selection_service() is None


def test_operator_configuration_rejects_arbitrary_authority(monkeypatch):
    monkeypatch.setattr(selection, '_SERVICE', None)
    with pytest.raises(ValueError, match='native app'):
        configure_program_app_startup(FastAPI(), object())


def test_actual_app_lifespan_verifies_before_starting_services(monkeypatch):
    from src.backend.app import application_lifespan
    from src.backend.live_strategy_runtime_service import LIVE_STRATEGY_RUNTIME
    from src.backend.market_discovery_runtime_service import MARKET_DISCOVERY_RUNTIME
    application, _, _, _ = supplied(monkeypatch)
    monkeypatch.setattr(LIVE_STRATEGY_RUNTIME, 'start', lambda: pytest.fail('Started before startup gate'))
    monkeypatch.setattr(MARKET_DISCOVERY_RUNTIME, 'start', lambda: pytest.fail('Started before startup gate'))
    async def fail(**kwargs):
        raise ValueError('Invalid central seal')
    monkeypatch.setattr(selection, 'load_certified_program_configuration', fail)
    async def run():
        async with application_lifespan(application):
            pytest.fail('Admitted app before central verification')
    with pytest.raises(ValueError, match='central seal'):
        asyncio.run(run())
    assert selection.program_configuration_selection_service() is None


def test_actual_lifespan_installs_before_services_and_http_selection(monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from src.backend import app as api
    from src.backend import live_strategy_runtime_service as live
    from src.backend import market_discovery_runtime_service as discovery
    from src.backend.backtest_history_performance import history_performance
    from src.backend.backtest_review_loading import review_loading
    from src.backend import backtest_configuration_option_reader as legacy
    application, service, revision, reads = supplied(monkeypatch)
    events = []
    def started(name):
        assert selection.program_configuration_selection_service() is service
        events.append(name + ':start')
    monkeypatch.setattr(live, 'LIVE_STRATEGY_RUNTIME', SimpleNamespace(
        start=lambda: started('live'), stop=lambda: events.append('live:stop')))
    monkeypatch.setattr(discovery, 'MARKET_DISCOVERY_RUNTIME', SimpleNamespace(
        start=lambda: started('discovery'), stop=lambda: events.append('discovery:stop')))
    async def close():
        events.append('reader:close')
    monkeypatch.setattr(history_performance, 'close', close)
    monkeypatch.setattr(review_loading, 'close', close)
    for name in ('_CANVAS_PROFILE_READ_EXECUTOR', '_CANVAS_PROFILE_WRITE_EXECUTOR', '_BACKTEST_HISTORY_READ_EXECUTOR'):
        monkeypatch.setattr(api, name, SimpleNamespace(shutdown=lambda **kwargs: events.append('executor:close')))
    monkeypatch.setattr(api, 'close_trading_journal', lambda: events.append('journal:close'))
    monkeypatch.setattr(legacy, 'certified_configuration_options', lambda *_: pytest.fail('Legacy catalogue read'))
    async def run():
        async with api.application_lifespan(application):
            assert events == ['live:start', 'discovery:start']
            async with AsyncClient(transport=ASGITransport(app=api.app), base_url='http://test') as client:
                response = await client.get('/api/trading/backtest/configuration-options',
                    params={'candidate_id': revision['revision_id']})
            assert response.status_code == 200
            assert response.json()['candidate_id'] == revision['revision_id']
    asyncio.run(run())
    assert len(reads) == 2
    assert events == ['live:start', 'discovery:start', 'reader:close', 'reader:close',
        'discovery:stop', 'live:stop', 'executor:close', 'executor:close', 'executor:close', 'journal:close']
