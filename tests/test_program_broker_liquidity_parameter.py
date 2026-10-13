"""Public request parameter must agree with the prepared native broker clock."""
import asyncio
from dataclasses import replace
from datetime import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.backend.app import BacktestRunCreateRequest, HistoricalPreflightRequest
from src.backend.replay_run_service import backtest_preflight
from tests.test_program_configuration_selection_service import installed
from tests.test_program_controller_runtime import definition_and_actor


@pytest.mark.parametrize('milliseconds', [100, 1000])
def test_public_backtest_models_accept_explicit_supported_broker_clock(milliseconds):
    request = BacktestRunCreateRequest(anchor_date='2026-10-12', broker_liquidity_resolution_ms=milliseconds)
    preflight = HistoricalPreflightRequest(mode='backtest', anchor_date='2026-10-12',
        broker_liquidity_resolution_ms=milliseconds)
    assert request.broker_liquidity_resolution_ms == preflight.broker_liquidity_resolution_ms == milliseconds


@pytest.mark.parametrize('milliseconds', [1, 200, 500, 2000, True, '100'])
def test_public_parameter_rejects_unsupported_or_untyped_clocks(milliseconds):
    with pytest.raises(ValidationError):
        BacktestRunCreateRequest(anchor_date='2026-10-12', broker_liquidity_resolution_ms=milliseconds)


def test_legacy_request_default_remains_unspecified():
    assert BacktestRunCreateRequest(anchor_date='2026-10-12').broker_liquidity_resolution_ms is None
    with pytest.raises(ValueError, match='current programme configuration'):
        backtest_preflight(anchor_date=definition_and_actor()[0].session_date, session_count=1,
            configuration_revision={'revision_id': 'legacy', 'payload': {}}, broker_liquidity_resolution_ms=1000)


def test_normal_preflight_route_forwards_requested_broker_clock(monkeypatch):
    from src.backend import app as api
    _, revision, _ = installed(monkeypatch)
    observed = []
    monkeypatch.setattr(api, 'backtest_configuration_snapshot', lambda *args, **kwargs: revision)
    monkeypatch.setattr(api, 'backtest_preflight', lambda **kwargs: observed.append(kwargs) or {'ready': False})
    payload = HistoricalPreflightRequest(mode='backtest', anchor_date='2026-10-13',
        configuration_revision_id=revision['revision_id'], broker_liquidity_resolution_ms=1000)
    assert api._trading_historical_preflight_payload(payload) == {'ready': False}
    assert observed[0]['broker_liquidity_resolution_ms'] == 1000
    assert observed[0]['_program_run_service'] is api.backtest_run_service


def test_normal_create_route_uses_programme_request_without_legacy_book(monkeypatch):
    from src.backend import app as api
    service, revision, _ = installed(monkeypatch)
    observed = []
    monkeypatch.setattr(api, 'backtest_configuration_snapshot', lambda *args, **kwargs: revision)
    def preflight(**kwargs):
        observed.append(kwargs)
        return dict(strategy_run_ready=True, prepared_run_id='prepared-test', execution_interval='1s',
            window={'sessions': ['2026-10-12']},
            market_data_plan=dict(token='synthetic-plan', execution_interval=dict(milliseconds=1000)),
            causal_v7_plan={},
            experimental_structure_fingerprint='')
    monkeypatch.setattr(api, 'backtest_preflight', preflight)
    async def launch(owner, definition, *, prepared_run_id):
        assert owner is api.backtest_run_service and prepared_run_id == 'prepared-test'
        assert definition.execution_interval == '1s'
        assert definition.experimental_structure_book == ''
        return SimpleNamespace(snapshot=lambda: {'run_id': 'synthetic-run'})
    monkeypatch.setattr(service, 'launch_reviewed_definition', launch)
    payload = BacktestRunCreateRequest(anchor_date='2026-10-13', session_count=1,
        configuration_revision_id=revision['revision_id'], start_time='04:00:00', end_time='09:30:00',
        broker_liquidity_resolution_ms=1000, experimental_structure_book='')
    assert asyncio.run(api.trading_backtest_run_create(payload)) == {'run_id': 'synthetic-run'}
    assert observed[0]['broker_liquidity_resolution_ms'] == 1000
    assert observed[0]['experimental_structure_book'] == ''


@pytest.mark.parametrize('milliseconds', [100, 1000])
def test_native_preflight_reports_selected_clock_and_rejects_changes(monkeypatch, milliseconds):
    service, revision, _ = installed(monkeypatch)
    definition, _ = definition_and_actor()
    definition = replace(definition, configuration_revision=revision,
        execution_interval={100: '100ms', 1000: '1s'}[milliseconds],
        market_data_plan={**definition.market_data_plan, 'execution_interval': {'milliseconds': milliseconds}})
    controller = SimpleNamespace(definition=definition, run_id='synthetic',
        _program_liquidity_resolution_ms=milliseconds)
    owner = object()
    async def staged(run_service):
        assert run_service is owner
        return controller
    monkeypatch.setattr(service, 'staged_controller', staged)
    scope = dict(configuration_revision=revision, sessions=(definition.session_date,),
        start_time=definition.start_time, end_time=time(20), initial_cash=definition.initial_cash)
    observed = asyncio.run(service.preflight(owner, **scope, broker_liquidity_resolution_ms=milliseconds))
    assert observed['broker_liquidity_resolution_ms'] == milliseconds
    assert observed['execution_interval'] == definition.execution_interval
    # These are the normal page's launch-enabling identity checks, not only
    # the programme's private prepared-run gate.
    assert observed['configuration_revision_id'] == revision['revision_id']
    assert observed['run_plan_id'] == revision['run_plan_id']
    assert observed['initial_cash'] == definition.initial_cash
    assert observed['configuration_content_hash'] == revision['content_hash']
    assert observed['configuration_revision'] == revision['revision']
    assert observed['available_run_plans'][0]['profile_id'] == revision['revision_id']
    assert observed['automatic_strategy_count'] == 1
    with pytest.raises(ValueError, match='prepared filling contract'):
        asyncio.run(service.preflight(owner, **scope,
            broker_liquidity_resolution_ms={100: 1000, 1000: 100}[milliseconds]))
    with pytest.raises(ValueError, match='prepared filling contract'):
        asyncio.run(service.preflight(owner, **scope, broker_liquidity_resolution_ms=True))
