"""Managed launch admission and resource ownership without operational I/O."""
import asyncio
from dataclasses import asdict
from hashlib import sha256
import json
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src.backend import program_app_bootstrap as bootstrap
from src.backend import program_configuration_selection_service as selection
from src.backend.program_app_startup import start_program_app_selection
from src.trading_runtime import strategy_registry as registry
from src.trading_runtime.keeper_session import ManagedKeeperSession
from tests.test_strategy_configuration_materialization import configured
from tests.test_program_configuration_selection_service import installed


def packet(tmp_path):
    _, actor, selected, _ = configured()
    header = actor.program_release.header
    registration = registry.ProgramStrategyExecutorRegistration(header['strategy_id'],
        header['strategy_number'], header['content_hash'], header['code_hash'], header['source_winner_hash'])
    fields = {k: v for k, v in asdict(registration).items()
              if k not in ('mode', 'evaluation_interval', 'requires_execution_risk')}
    plan = dict(contract='approved-program-app-startup-v1',
        approved_selections={selected.header['configuration_id']: selected.header['content_hash']},
        executors=[fields], concurrency=2)
    path = tmp_path / 'approved-startup.json'
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    return str(path), sha256(raw).hexdigest(), plan, actor.program_release


def test_startup_v3_has_separate_explicit_execution_risk_approval(tmp_path):
    path, _, plan, _ = packet(tmp_path)
    identity = next(iter(plan['approved_selections']))
    plan.update(contract='approved-program-app-startup-v3',
        approved_execution_bindings={identity: 'b' * 64}, approved_execution_risks={identity: 'c' * 64})
    plan['executors'][0]['requires_execution_risk'] = True
    raw = json.dumps(plan).encode()
    from pathlib import Path
    Path(path).write_bytes(raw)
    approved = bootstrap.read_startup_plan(path, sha256(raw).hexdigest())
    assert len(approved) == 5 and approved[3][identity] == 'b' * 64 and approved[4][identity] == 'c' * 64
    plan['approved_execution_risks'] = {'00000000-0000-0000-0000-000000000099': 'c' * 64}
    raw = json.dumps(plan).encode()
    Path(path).write_bytes(raw)
    with pytest.raises(ValueError, match='risk approvals'):
        bootstrap.read_startup_plan(path, sha256(raw).hexdigest())


def supplied(tmp_path, monkeypatch):
    path, digest, plan, release = packet(tmp_path)
    _, revision, reads = installed(monkeypatch)
    monkeypatch.setattr(selection, '_SERVICE', None)
    monkeypatch.setattr(registry, '_PROGRAM_REGISTRY', {})
    events = []
    class Keeper:
        connected = True
        client_state = 'CONNECTED'
        def add_listener(self, callback): pass
        def remove_listener(self, callback): pass
        def stop(self): self.connected = False
        def close(self): events.append('keeper:close')
    session = ManagedKeeperSession(Keeper())
    session._on_state('CONNECTED')
    def opened():
        events.append('keeper:open')
        return session
    monkeypatch.setattr(bootstrap, 'open_workstation_keeper_session', opened)
    async def cold(factory, **identity):
        assert not registry._PROGRAM_REGISTRY
        assert identity['expected_content_hash'] == release.header['content_hash']
        events.append('release:read')
        return release
    monkeypatch.setattr(bootstrap, 'load_program_release', cold)
    original = registry.register_program_strategy_executor
    def register(value):
        events.append('registry:register')
        original(value)
    monkeypatch.setattr(bootstrap, 'register_program_strategy_executor', register)
    application = bootstrap.configure_managed_program_startup(FastAPI(), path, digest)
    return application, session, revision, reads, events


def test_hash_bound_plan_is_only_approval_references(tmp_path):
    path, digest, plan, _ = packet(tmp_path)
    pins, executors, concurrency = bootstrap.read_startup_plan(path, digest)
    assert dict(pins) == plan['approved_selections']
    assert len(executors) == 1 and concurrency == 2
    with pytest.raises(TypeError):
        pins['new'] = 'a' * 64
    with pytest.raises(ValueError, match='file hash'):
        bootstrap.read_startup_plan(path, '0' * 64)


@pytest.mark.parametrize('missing', [False, True])
def test_v2_startup_requires_exact_execution_approvals_for_every_selection(tmp_path, missing):
    path, _, plan, _ = packet(tmp_path)
    plan['contract'] = 'approved-program-app-startup-v2'
    plan['approved_execution_bindings'] = ({} if missing else
        {identity: 'b' * 64 for identity in plan['approved_selections']})
    raw = json.dumps(plan).encode()
    from pathlib import Path
    Path(path).write_bytes(raw)
    if missing:
        with pytest.raises(ValueError, match='execution binding pins'):
            bootstrap.read_startup_plan(path, sha256(raw).hexdigest())
    else:
        result = bootstrap.read_startup_plan(path, sha256(raw).hexdigest())
        assert dict(result[3]) == plan['approved_execution_bindings']


@pytest.mark.parametrize('mutation', ['extra', 'empty', 'duplicate', 'mode', 'concurrency'])
def test_invalid_plan_cannot_install_or_open_keeper(tmp_path, monkeypatch, mutation):
    path, _, plan, _ = packet(tmp_path)
    if mutation == 'extra': plan['trading_parameters'] = {'entry_cash': 5}
    elif mutation == 'empty': plan['approved_selections'] = {}
    elif mutation == 'duplicate': plan['executors'] *= 2
    elif mutation == 'mode': plan['executors'][0]['mode'] = 'live'
    else: plan['concurrency'] = True
    raw = json.dumps(plan).encode()
    from pathlib import Path
    Path(path).write_bytes(raw)
    monkeypatch.setattr(bootstrap, 'open_workstation_keeper_session', lambda:
        pytest.fail('Opened Keeper for invalid approval'))
    with pytest.raises(ValueError):
        bootstrap.read_startup_plan(path, sha256(raw).hexdigest())


def test_native_selection_installs_only_after_cold_reads_then_owned_close(tmp_path, monkeypatch):
    application, session, _, reads, events = supplied(tmp_path, monkeypatch)
    assert events == []
    assert asyncio.run(start_program_app_selection(application)) == 1
    assert events == ['release:read', 'keeper:open', 'registry:register']
    assert len(reads) == 1 and selection.program_configuration_selection_service() is not None
    asyncio.run(bootstrap.close_managed_program_startup(application))
    asyncio.run(bootstrap.close_managed_program_startup(application))
    assert events[-1] == 'keeper:close' and events.count('keeper:close') == 1
    assert not session.writable


@pytest.mark.parametrize('error', [ValueError('Bad central seal'), asyncio.CancelledError()])
def test_selection_failure_or_cancellation_closes_owned_session_without_install(tmp_path, monkeypatch, error):
    application, session, _, _, events = supplied(tmp_path, monkeypatch)
    async def fail(**kwargs): raise error
    monkeypatch.setattr(selection, 'load_certified_program_configuration', fail)
    with pytest.raises(type(error)):
        asyncio.run(start_program_app_selection(application))
    assert events[-1] == 'keeper:close'
    assert selection.program_configuration_selection_service() is None and not session.writable


def test_cancelled_session_open_is_drained_and_closed(tmp_path, monkeypatch):
    _, session, _, _, events = supplied(tmp_path, monkeypatch)
    entered, release = Event(), Event()
    def opened():
        entered.set()
        assert release.wait(5)
        return session
    monkeypatch.setattr(bootstrap, 'open_workstation_keeper_session', opened)
    async def run():
        job = asyncio.create_task(bootstrap._open_owned_session())
        assert await asyncio.to_thread(entered.wait, 5)
        job.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError): await job
    asyncio.run(run())
    assert events == ['keeper:close'] and not session.writable


def test_policy_session_close_during_selection_proof_prevents_install(tmp_path, monkeypatch):
    application, session, _, _, _ = supplied(tmp_path, monkeypatch)
    original = selection.load_certified_program_configuration
    async def close_during_read(**kwargs):
        result = await original(**kwargs)
        await bootstrap.close_managed_program_startup(application)
        return result
    monkeypatch.setattr(selection, 'load_certified_program_configuration', close_during_read)
    with pytest.raises(RuntimeError, match='owner changed'):
        asyncio.run(start_program_app_selection(application))
    assert selection.program_configuration_selection_service() is None and not session.writable


def test_actual_lifespan_owns_managed_session_without_starting_real_services(tmp_path, monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from src.backend import app as api
    from src.backend import live_strategy_runtime_service as live
    from src.backend import market_discovery_runtime_service as discovery
    from src.backend.backtest_history_performance import history_performance
    from src.backend.backtest_review_loading import review_loading
    application, session, revision, _, events = supplied(tmp_path, monkeypatch)
    def start(name):
        assert selection.program_configuration_selection_service() is not None
        events.append(name + ':start')
    monkeypatch.setattr(live, 'LIVE_STRATEGY_RUNTIME', SimpleNamespace(start=lambda: start('live'), stop=lambda: None))
    monkeypatch.setattr(discovery, 'MARKET_DISCOVERY_RUNTIME', SimpleNamespace(start=lambda: start('discovery'), stop=lambda: None))
    async def close(): pass
    monkeypatch.setattr(history_performance, 'close', close)
    monkeypatch.setattr(review_loading, 'close', close)
    for name in ('_CANVAS_PROFILE_READ_EXECUTOR', '_CANVAS_PROFILE_WRITE_EXECUTOR', '_BACKTEST_HISTORY_READ_EXECUTOR'):
        monkeypatch.setattr(api, name, SimpleNamespace(shutdown=lambda **kw: None))
    monkeypatch.setattr(api, 'close_trading_journal', lambda: None)
    async def run():
        async with api.application_lifespan(application):
            assert session.writable
            assert events[-2:] == ['live:start', 'discovery:start']
            async with AsyncClient(transport=ASGITransport(app=api.app), base_url='http://test') as client:
                response = await client.get('/api/trading/backtest/configuration-options',
                    params={'candidate_id': revision['revision_id']})
            assert response.status_code == 200
            assert response.json()['candidate_id'] == revision['revision_id']
    asyncio.run(run())
    assert events[-1] == 'keeper:close' and not session.writable


def test_normal_launcher_uses_existing_app_factory_only_when_explicit(tmp_path, monkeypatch):
    import sys
    from scripts import run_backend
    path, digest, _, _ = packet(tmp_path)
    monkeypatch.setattr(run_backend, '_apply_service_environment', lambda: None)
    monkeypatch.setattr(run_backend, '_restore_stdlib_ssl_for_keeper', lambda: None)
    calls = []
    monkeypatch.setitem(sys.modules, 'uvicorn', SimpleNamespace(run=lambda *args, **kw: calls.append((args, kw))))
    monkeypatch.setattr(sys, 'argv', ['run_backend.py', '--approved-program-startup-plan', path,
        '--approved-program-startup-plan-sha256', digest])
    monkeypatch.setenv('PROGRAM_APP_STARTUP_PLAN', '')
    monkeypatch.setenv('PROGRAM_APP_STARTUP_PLAN_SHA256', '')
    assert run_backend.main() == 0
    assert calls[0][0] == ('src.backend.program_app_bootstrap:create_app',)
    assert calls[0][1]['factory'] is True and calls[0][1]['lifespan'] == 'on'
    assert run_backend.os.environ['PROGRAM_APP_STARTUP_PLAN'] == path
    assert run_backend.os.environ['PROGRAM_APP_STARTUP_PLAN_SHA256'] == digest


@pytest.mark.parametrize('args', [
    ['--approved-program-startup-plan', 'missing.json'],
    ['--approved-program-startup-plan', 'missing.json', '--approved-program-startup-plan-sha256', 'bad'],
])
def test_incomplete_launcher_approval_fails_before_private_environment_load(monkeypatch, args):
    import sys
    from scripts import run_backend
    monkeypatch.setattr(run_backend, '_apply_service_environment', lambda:
        pytest.fail('Read private environment for malformed startup approval'))
    monkeypatch.setattr(sys, 'argv', ['run_backend.py', *args])
    with pytest.raises(SystemExit) as error:
        run_backend.main()
    assert error.value.code == 2
